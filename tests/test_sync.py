"""End-to-end tests using real temporary git repositories.

Run:  python -m unittest discover -s tests -v
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PERSONAL = {"id": "personal", "label": "Personal", "name": "Kevin Real", "email": "kevin@personal.example"}
ALIAS = {"id": "alias", "label": "Alias", "name": "shadowdev", "email": "shadow@alias.example", "disable_signing": True}


def git(cwd, *args, name=None, email=None, input=None):
    env = os.environ.copy()
    if name:
        env.update({"GIT_AUTHOR_NAME": name, "GIT_COMMITTER_NAME": name})
    if email:
        env.update({"GIT_AUTHOR_EMAIL": email, "GIT_COMMITTER_EMAIL": email})
    r = subprocess.run(["git", "-c", "commit.gpgsign=false", *args], cwd=cwd, env=env, capture_output=True, text=True, input=input)
    if r.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {r.stderr}")
    return r.stdout


def commit(cwd, fname, content, msg, who):
    Path(cwd, fname).write_text(content)
    git(cwd, "add", "-A")
    git(cwd, "commit", "-q", "-m", msg, name=who["name"], email=who["email"])
    return git(cwd, "rev-parse", "HEAD").strip()


def log(cwd):
    """[(author, author_email, committer, committer_email, subject)] oldest first."""
    out = git(cwd, "log", "--reverse", "--format=%an|%ae|%cn|%ce|%s")
    return [tuple(l.split("|")) for l in out.splitlines()]


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        os.environ["GIT_PROXY_HOME"] = str(base / "state")
        os.environ["GIT_CONFIG_GLOBAL"] = str(base / "empty-gitconfig")
        os.environ["GIT_CONFIG_NOSYSTEM"] = "1"
        (base / "empty-gitconfig").write_text("")
        from gitproxy import store, sync

        self.store, self.sync = store, sync
        self.orig = base / "orig"
        self.mirror = base / "mirror"
        self.orig.mkdir()
        git(self.orig, "init", "-q", "-b", "main")
        cfg = store.load_config()
        cfg["profiles"]["personal"] = store.normalize_profile(PERSONAL)
        cfg["profiles"]["alias"] = store.normalize_profile(ALIAS)
        cfg["pairs"]["p1"] = store.normalize_pair(
            {"id": "p1", "label": "t", "original_path": str(self.orig), "original_profile": "personal", "mirror_path": str(self.mirror), "mirror_profile": "alias", "scrub": True}
        )
        store.save_config(cfg)

    def tearDown(self):
        self.tmp.cleanup()

    def engine(self):
        return self.sync.Engine("p1")

    def test_round_trip(self):
        E, TO_MIRROR, TO_ORIGINAL = self.sync.Engine, self.sync.TO_MIRROR, self.sync.TO_ORIGINAL
        commit(self.orig, "a.txt", "hello\n", "Add a.txt\n\nSigned-off-by: Kevin Real <kevin@personal.example>", PERSONAL)
        commit(self.orig, "b.txt", "b\n", "Add b.txt", PERSONAL)

        self.engine().setup("mirror", lambda *_: None)
        self.assertTrue(self.sync.is_repo(self.mirror))
        self.assertEqual(git(self.mirror, "config", "user.name").strip(), "shadowdev")
        self.assertEqual(git(self.mirror, "config", "commit.gpgsign").strip(), "false")

        res = self.engine().sync(TO_MIRROR, lambda *_: None)
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["replayed"], 2)
        self.assertEqual(log(self.mirror), [
            ("shadowdev", "shadow@alias.example", "shadowdev", "shadow@alias.example", "Add a.txt"),
            ("shadowdev", "shadow@alias.example", "shadowdev", "shadow@alias.example", "Add b.txt"),
        ])
        body = git(self.mirror, "log", "-1", "--format=%B", "HEAD~1")
        self.assertIn("Signed-off-by: shadowdev <shadow@alias.example>", body)
        self.assertNotIn("Kevin", body)
        # no trace of the personal identity anywhere in the mirror's objects
        allobjs = git(self.mirror, "cat-file", "--batch-all-objects", "--batch-check")
        for sha in [l.split()[0] for l in allobjs.splitlines() if "commit" in l]:
            self.assertNotIn("kevin@personal", git(self.mirror, "cat-file", "-p", sha))
        # original untouched
        self.assertEqual([l[0] for l in log(self.orig)], ["Kevin Real", "Kevin Real"])

        # friends work on the mirror
        friend = {"name": "Friend One", "email": "friend@example.com"}
        commit(self.mirror, "c.txt", "c\n", "Friend adds c", friend)
        commit(self.mirror, "a.txt", "hello friend\n", "Friend edits a", friend)
        # and the user commits directly in the mirror as the alias
        commit(self.mirror, "d.txt", "d\n", "Alias adds d", ALIAS)

        res = self.engine().sync(TO_ORIGINAL, lambda *_: None)
        self.assertEqual((res["status"], res["replayed"]), ("ok", 3))
        self.assertEqual(log(self.orig)[2:], [
            ("guest", "guest@users.noreply.local", "Kevin Real", "kevin@personal.example", "Friend adds c"),
            ("guest", "guest@users.noreply.local", "Kevin Real", "kevin@personal.example", "Friend edits a"),
            ("Kevin Real", "kevin@personal.example", "Kevin Real", "kevin@personal.example", "Alias adds d"),
        ])
        self.assertEqual((self.orig / "a.txt").read_text(), "hello friend\n")

        # user keeps working on the original
        commit(self.orig, "e.txt", "e\n", "Kevin adds e", PERSONAL)
        self.assertEqual(len(self.engine().plan(TO_MIRROR)), 1)  # guest commits are NOT replayed back
        res = self.engine().sync(TO_MIRROR, lambda *_: None)
        self.assertEqual((res["status"], res["replayed"]), ("ok", 1))
        m = log(self.mirror)
        self.assertEqual(m[2][0], "Friend One")  # friend commits untouched
        self.assertEqual(m[3][0], "Friend One")
        self.assertEqual(m[-1], ("shadowdev", "shadow@alias.example", "shadowdev", "shadow@alias.example", "Kevin adds e"))
        self.assertEqual(self.engine().plan(TO_MIRROR), [])
        self.assertEqual(self.engine().plan(TO_ORIGINAL), [])
        self.assertEqual(git(self.orig, "rev-parse", "HEAD^{tree}"), git(self.mirror, "rev-parse", "HEAD^{tree}"))
        st = self.engine().status()
        self.assertEqual((st["original"]["unsynced"], st["mirror"]["unsynced"], st["pending"]), (0, 0, None))

    def test_conflict_abort_and_continue(self):
        TO_MIRROR, TO_ORIGINAL = self.sync.TO_MIRROR, self.sync.TO_ORIGINAL
        commit(self.orig, "f.txt", "one\n", "base", PERSONAL)
        self.engine().setup("mirror", lambda *_: None)
        self.engine().sync(TO_MIRROR, lambda *_: None)
        friend = {"name": "Friend", "email": "friend@example.com"}
        commit(self.mirror, "f.txt", "friend\n", "friend edit", friend)
        commit(self.mirror, "g.txt", "g\n", "friend adds g", friend)
        commit(self.orig, "f.txt", "kevin\n", "kevin edit", PERSONAL)
        head = git(self.orig, "rev-parse", "HEAD").strip()

        res = self.engine().sync(TO_ORIGINAL, lambda *_: None)
        self.assertEqual(res["status"], "conflict")
        self.assertEqual(res["files"], ["f.txt"])
        self.assertIsNotNone(self.engine().status()["pending"])
        with self.assertRaises(self.sync.SyncError):
            self.engine().sync(TO_ORIGINAL, lambda *_: None)

        self.engine().abort_sync(lambda *_: None)
        self.assertEqual(git(self.orig, "rev-parse", "HEAD").strip(), head)
        self.assertTrue(self.sync.is_clean(self.orig))
        self.assertIsNone(self.engine().status()["pending"])

        res = self.engine().sync(TO_ORIGINAL, lambda *_: None)
        self.assertEqual(res["status"], "conflict")
        (self.orig / "f.txt").write_text("merged\n")
        git(self.orig, "add", "f.txt")
        res = self.engine().continue_sync(lambda *_: None)
        self.assertEqual((res["status"], res["replayed"]), ("ok", 2))
        self.assertEqual(log(self.orig)[-2:], [
            ("guest", "guest@users.noreply.local", "Kevin Real", "kevin@personal.example", "friend edit"),
            ("guest", "guest@users.noreply.local", "Kevin Real", "kevin@personal.example", "friend adds g"),
        ])
        self.assertEqual(self.engine().plan(TO_ORIGINAL), [])

    def test_duplicate_change_is_skipped(self):
        TO_MIRROR = self.sync.TO_MIRROR
        commit(self.orig, "a.txt", "x\n", "base", PERSONAL)
        self.engine().setup("mirror", lambda *_: None)
        self.engine().sync(TO_MIRROR, lambda *_: None)
        commit(self.mirror, "a.txt", "y\n", "same change on mirror", ALIAS)
        commit(self.orig, "a.txt", "y\n", "same change on orig", PERSONAL)
        # the mirror side has an unmapped commit too; pull it first (restore), which is empty vs original
        res = self.engine().sync(self.sync.TO_ORIGINAL, lambda *_: None)
        self.assertEqual((res["status"], res["replayed"], res["skipped"]), ("ok", 0, 1))
        res = self.engine().sync(TO_MIRROR, lambda *_: None)
        self.assertEqual((res["status"], res["replayed"], res["skipped"]), ("ok", 0, 1))
        self.assertEqual(self.engine().plan(TO_MIRROR), [])
        self.assertEqual(len(self.engine().history()), 3)

    def test_merge_commits_from_mirror(self):
        TO_ORIGINAL = self.sync.TO_ORIGINAL
        commit(self.orig, "a.txt", "x\n", "base", PERSONAL)
        self.engine().setup("mirror", lambda *_: None)
        self.engine().sync(self.sync.TO_MIRROR, lambda *_: None)
        friend = {"name": "Friend", "email": "friend@example.com"}
        git(self.mirror, "checkout", "-q", "-b", "feature")
        commit(self.mirror, "feat.txt", "f\n", "feature work", friend)
        git(self.mirror, "checkout", "-q", "main")
        commit(self.mirror, "main.txt", "m\n", "main work", ALIAS)
        git(self.mirror, "merge", "-q", "--no-ff", "-m", "Merge feature", "feature", name="Friend", email="friend@example.com")
        res = self.engine().sync(TO_ORIGINAL, lambda *_: None)
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["replayed"], 2)
        self.assertEqual(res["skipped"], 1)  # the merge itself is empty once both sides are replayed
        self.assertTrue((self.orig / "feat.txt").exists())
        self.assertTrue((self.orig / "main.txt").exists())
        self.assertEqual(self.engine().plan(TO_ORIGINAL), [])

    def test_strip_coauthor_trailers(self):
        msg = "Implement thing\n\nSome body text.\n\nCo-authored-by: Copilot <copilot@github.com>\nco-Authored-By: Bob <bob@x.com>\nSigned-off-by: Kevin Real <kevin@personal.example>"
        commit(self.orig, "a.txt", "x\n", msg, PERSONAL)
        self.engine().setup("mirror", lambda *_: None)
        self.engine().sync(self.sync.TO_MIRROR, lambda *_: None)
        body = git(self.mirror, "log", "-1", "--format=%B").strip()
        self.assertEqual(body, "Implement thing\n\nSome body text.\n\nSigned-off-by: shadowdev <shadow@alias.example>")
        # friend commit with a co-author trailer is copied untouched
        friend = {"name": "Friend", "email": "friend@example.com"}
        commit(self.mirror, "b.txt", "b\n", "Friend work\n\nCo-authored-by: Pal <pal@x.com>", friend)
        self.engine().sync(self.sync.TO_ORIGINAL, lambda *_: None)
        self.assertIn("Co-authored-by: Pal", git(self.orig, "log", "-1", "--format=%B"))

    def test_https_credentials_apply(self):
        cfg = self.store.load_config()
        cfg["profiles"]["alias"].update({"auth_type": "https", "username": "shadowdev", "token": "ghp_secret", "host": "github.com"})
        cfg["pairs"]["p1"]["mirror_remote"] = "https://github.com/shadowdev/project.git"
        self.store.save_config(cfg)
        self.engine().setup("mirror", lambda *_: None)
        helpers = git(self.mirror, "config", "--get-all", "credential.helper").splitlines()
        self.assertEqual(helpers[0], "")
        self.assertTrue(helpers[1].startswith('store --file="'))
        cred = self.store.cred_path("alias")
        self.assertEqual(cred.read_text().strip(), "https://shadowdev:ghp_secret@github.com")
        self.assertEqual(git(self.mirror, "remote", "get-url", "origin").strip(), "https://github.com/shadowdev/project.git")
        # git can actually resolve the credential through the configured helper
        out = git(self.mirror, "credential", "fill", input="protocol=https\nhost=github.com\n\n")
        self.assertIn("password=ghp_secret", out)


if __name__ == "__main__":
    unittest.main()
