"""Replay engine.

Copies commits from one repo of a pair to the other, rewriting authorship:

  original -> mirror   (TO_MIRROR)
      commits authored by *you* (personal profile email, or any extra "my emails")
      are re-authored as the alias profile.  Everything else is copied verbatim.
      Committer is always the alias profile, commits are never signed.

  mirror -> original   (TO_ORIGINAL)
      commits authored by the alias are restored to your personal identity.
      Everything else (your friends) is re-authored as the "guest" identity.
      Committer is always the personal profile.

Every replayed commit is recorded in a mapping file (original sha <-> mirror sha) so
it is never replayed twice, in either direction.  That is what makes a round trip
work: a friend's commit arrives in the original as "guest", and when you later sync
back to the mirror it is skipped because the mirror already has the real one.
"""
from __future__ import annotations

import datetime as _dt
import shlex
from dataclasses import asdict, dataclass
from pathlib import Path

import re

from . import store
from .gitcmd import GitError, run

TO_MIRROR = "to-mirror"
TO_ORIGINAL = "to-original"
DIRECTIONS = (TO_MIRROR, TO_ORIGINAL)


class SyncError(Exception):
    pass


# ---------------------------------------------------------------- data


@dataclass
class Commit:
    sha: str
    parents: list
    author_name: str
    author_email: str
    author_date: str
    committer_name: str
    committer_email: str
    committer_date: str
    message: str

    @property
    def subject(self) -> str:
        s = self.message.strip()
        return s.splitlines()[0] if s else "(no message)"

    @property
    def is_merge(self) -> bool:
        return len(self.parents) > 1


@dataclass
class Planned:
    commit: Commit
    action: str  # rewrite | restore | guest | keep
    new_name: str
    new_email: str
    committer_name: str
    committer_email: str
    message: str

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Planned":
        d = dict(d)
        d["commit"] = Commit(**d["commit"])
        return cls(**d)

    def summary(self) -> dict:
        c = self.commit
        return {
            "sha": c.sha,
            "short": c.sha[:8],
            "subject": c.subject,
            "is_merge": c.is_merge,
            "action": self.action,
            "from": f"{c.author_name} <{c.author_email}>",
            "to": f"{self.new_name} <{self.new_email}>",
            "date": c.author_date,
            "message_changed": self.message != c.message,
        }


@dataclass
class Side:
    key: str  # original | mirror
    path: str
    branch: str
    profile: dict
    remote: str
    mapped: set


# ---------------------------------------------------------------- git helpers

_REC = "\x1e"
_FLD = "\x1f"
_FORMAT = _REC + _FLD.join(["%H", "%P", "%an", "%ae", "%aI", "%cn", "%ce", "%cI", "%B"])


def log_commits(path, rev) -> list:
    out = run(["log", "--reverse", "--topo-order", "--format=" + _FORMAT, rev], cwd=path).stdout
    commits = []
    for rec in out.split(_REC):
        if not rec.strip():
            continue
        f = rec.split(_FLD, 8)
        if len(f) < 9:
            continue
        commits.append(
            Commit(
                sha=f[0],
                parents=f[1].split(),
                author_name=f[2],
                author_email=f[3],
                author_date=f[4],
                committer_name=f[5],
                committer_email=f[6],
                committer_date=f[7],
                message=f[8].rstrip("\n"),
            )
        )
    return commits


def is_repo(path) -> bool:
    p = Path(path)
    if not p.is_dir():
        return False
    r = run(["rev-parse", "--show-toplevel"], cwd=p, check=False)
    if r.returncode != 0:
        return False
    try:
        return Path(r.stdout.strip()).resolve() == p.resolve()
    except OSError:
        return False


def head_sha(path):
    r = run(["rev-parse", "--verify", "-q", "HEAD"], cwd=path, check=False)
    return r.stdout.strip() or None


def current_branch(path):
    r = run(["symbolic-ref", "--short", "-q", "HEAD"], cwd=path, check=False)
    return r.stdout.strip() or None


def branch_exists(path, branch) -> bool:
    return run(["rev-parse", "--verify", "-q", f"refs/heads/{branch}"], cwd=path, check=False).returncode == 0


def is_clean(path) -> bool:
    return run(["status", "--porcelain", "--untracked-files=no"], cwd=path).stdout.strip() == ""


def unmerged_files(path) -> list:
    out = run(["diff", "--name-only", "--diff-filter=U"], cwd=path).stdout
    return [l for l in out.splitlines() if l.strip()]


def staged_nonempty(path) -> bool:
    return run(["diff", "--cached", "--quiet"], cwd=path, check=False).returncode == 1


def git_path(path, name) -> Path:
    return Path(run(["rev-parse", "--git-path", name], cwd=path).stdout.strip())


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).astimezone().isoformat(timespec="seconds")


# ---------------------------------------------------------------- engine


class Engine:
    def __init__(self, pair_id: str, cfg: dict | None = None):
        self.cfg = cfg or store.load_config()
        if pair_id not in self.cfg["pairs"]:
            raise SyncError(f"Unknown pair: {pair_id}")
        self.pair_id = pair_id
        self.pair = store.normalize_pair(self.cfg["pairs"][pair_id])
        self.map = store.load_map(pair_id)
        self.mapped_original = {e["original"] for e in self.map["entries"] if e.get("original")}
        self.mapped_mirror = {e["mirror"] for e in self.map["entries"] if e.get("mirror")}

    # -- config access

    def profile(self, pid: str) -> dict:
        p = self.cfg["profiles"].get(pid)
        if not p:
            raise SyncError(f"Profile not configured: {pid or '(none)'}. Edit the pair and pick a profile.")
        return store.normalize_profile(p)

    def side(self, key: str) -> Side:
        pr = self.pair
        if key == "original":
            return Side("original", pr["original_path"], pr["original_branch"], self.profile(pr["original_profile"]), pr["original_remote"], self.mapped_original)
        if key == "mirror":
            return Side("mirror", pr["mirror_path"], pr["mirror_branch"], self.profile(pr["mirror_profile"]), pr["mirror_remote"], self.mapped_mirror)
        raise SyncError(f"Unknown side: {key}")

    def sides(self, direction: str):
        if direction not in DIRECTIONS:
            raise SyncError(f"Unknown direction: {direction} (use {TO_MIRROR} or {TO_ORIGINAL})")
        o, m = self.side("original"), self.side("mirror")
        return (o, m) if direction == TO_MIRROR else (m, o)

    # -- planning

    def _my_emails(self) -> set:
        personal = self.side("original").profile
        emails = {personal["email"].lower()} | {e.lower() for e in self.pair["my_emails"]}
        emails.discard("")
        return emails

    @staticmethod
    def _rewrite_message(msg: str, src: dict, dst: dict) -> str:
        if src["email"] and dst["email"] and src["email"] != dst["email"]:
            msg = msg.replace(src["email"], dst["email"])
        if src["name"] and dst["name"] and src["name"] != dst["name"] and len(src["name"]) >= 3:
            msg = msg.replace(src["name"], dst["name"])
        return msg

    def _strip_trailers(self, msg: str) -> str:
        """Drop trailer lines such as 'Co-authored-by: Copilot <...>' (configured per pair, case-insensitive)."""
        keys = [k for k in self.pair["strip_trailers"] if k]
        if not keys:
            return msg
        pat = re.compile(r"^\s*(" + "|".join(re.escape(k) for k in keys) + r")\s*:", re.IGNORECASE)
        lines = [l for l in msg.splitlines() if not pat.match(l)]
        out = "\n".join(lines)
        out = re.sub(r"\n{3,}", "\n\n", out).rstrip()
        return out

    def _my_message(self, msg: str, src: dict, dst: dict) -> str:
        if self.pair["rewrite_messages"]:
            msg = self._rewrite_message(msg, src, dst)
        return self._strip_trailers(msg)

    def decide(self, direction: str, c: Commit) -> Planned:
        personal = self.side("original").profile
        alias = self.side("mirror").profile
        if direction == TO_MIRROR:
            mine = c.author_email.lower() in self._my_emails() or (personal["name"] and c.author_name == personal["name"])
            if mine:
                msg = self._my_message(c.message, personal, alias)
                return Planned(c, "rewrite", alias["name"], alias["email"], alias["name"], alias["email"], msg)
            return Planned(c, "keep", c.author_name, c.author_email, alias["name"], alias["email"], c.message)
        # TO_ORIGINAL
        is_alias = (alias["email"] and c.author_email.lower() == alias["email"].lower()) or (alias["name"] and c.author_name == alias["name"])
        if is_alias:
            msg = self._my_message(c.message, alias, personal)
            return Planned(c, "restore", personal["name"], personal["email"], personal["name"], personal["email"], msg)
        return Planned(c, "guest", self.pair["guest_name"], self.pair["guest_email"], personal["name"], personal["email"], c.message)

    def plan(self, direction: str) -> list:
        src, dst = self.sides(direction)
        if not is_repo(src.path):
            raise SyncError(f"{src.key} is not a git repository: {src.path}")
        if not branch_exists(src.path, src.branch):
            return []
        commits = log_commits(src.path, src.branch)
        return [self.decide(direction, c) for c in commits if c.sha not in src.mapped]

    # -- status

    def side_status(self, key: str) -> dict:
        s = self.side(key)
        info = {
            "key": key,
            "path": s.path,
            "branch": s.branch,
            "remote": s.remote,
            "profile": {"id": s.profile["id"], "label": s.profile["label"], "name": s.profile["name"], "email": s.profile["email"]},
            "exists": Path(s.path).is_dir() if s.path else False,
            "is_repo": False,
            "current_branch": None,
            "head": None,
            "clean": None,
            "unsynced": None,
            "identity": None,
            "has_remote": False,
        }
        if not (s.path and is_repo(s.path)):
            return info
        info["is_repo"] = True
        info["current_branch"] = current_branch(s.path)
        info["head"] = head_sha(s.path)
        info["clean"] = is_clean(s.path)
        name = run(["config", "--get", "user.name"], cwd=s.path, check=False).stdout.strip()
        email = run(["config", "--get", "user.email"], cwd=s.path, check=False).stdout.strip()
        info["identity"] = f"{name} <{email}>" if name or email else None
        info["identity_ok"] = (name == s.profile["name"] and email == s.profile["email"])
        info["has_remote"] = run(["remote", "get-url", "origin"], cwd=s.path, check=False).returncode == 0
        direction = TO_MIRROR if key == "original" else TO_ORIGINAL
        try:
            info["unsynced"] = len(self.plan(direction))
        except (SyncError, GitError):
            info["unsynced"] = None
        return info

    def status(self) -> dict:
        pending = store.load_pending(self.pair_id)
        pend = None
        if pending:
            src, dst = self.sides(pending["direction"])
            files = unmerged_files(dst.path) if is_repo(dst.path) else []
            pend = {
                "direction": pending["direction"],
                "dst": dst.key,
                "dst_path": dst.path,
                "subject": pending["current"]["commit"]["message"].strip().splitlines()[0] if pending["current"]["commit"]["message"].strip() else "",
                "sha": pending["current"]["commit"]["sha"][:8],
                "unresolved": files,
                "remaining": len(pending["queue"]),
            }
        entries = self.map["entries"]
        return {
            "pair": self.pair,
            "original": self.side_status("original"),
            "mirror": self.side_status("mirror"),
            "pending": pend,
            "mapped": len(entries),
            "last_sync": entries[-1]["time"] if entries else None,
        }

    def history(self, limit: int = 200) -> list:
        return list(reversed(self.map["entries"][-limit:]))

    # -- syncing

    def _checkout(self, dst: Side, log):
        if current_branch(dst.path) == dst.branch:
            return
        if branch_exists(dst.path, dst.branch):
            run(["checkout", "--quiet", dst.branch], cwd=dst.path)
        elif head_sha(dst.path) is None:
            run(["symbolic-ref", "HEAD", f"refs/heads/{dst.branch}"], cwd=dst.path)
        else:
            run(["checkout", "--quiet", "-b", dst.branch], cwd=dst.path)
        log(f"[{dst.key}] checked out {dst.branch}")

    def sync(self, direction: str, log=print) -> dict:
        if store.load_pending(self.pair_id):
            raise SyncError("A sync is paused on a conflict. Resolve and continue, or abort it first.")
        src, dst = self.sides(direction)
        if not is_repo(src.path):
            raise SyncError(f"{src.key} is not a git repository: {src.path}")
        if not is_repo(dst.path):
            raise SyncError(f"{dst.key} is not a git repository: {dst.path}. Run Setup on it first.")
        if not is_clean(dst.path):
            raise SyncError(f"{dst.key} has uncommitted changes. Commit or stash them first: {dst.path}")
        self._checkout(dst, log)
        queue = self.plan(direction)
        if not queue:
            log(f"Nothing to sync: {src.key} has no commits the {dst.key} does not already have.")
            return {"status": "nothing", "replayed": 0, "skipped": 0}
        log(f"{len(queue)} commit(s) to replay {src.key} -> {dst.key}")
        self._fetch(src, dst, log)
        return self._replay_queue(direction, src, dst, queue, log)

    def _fetch(self, src: Side, dst: Side, log):
        args = ["fetch", "--quiet", "--no-tags", "--no-write-fetch-head", src.path, src.branch]
        r = run(args, cwd=dst.path, check=False)
        if r.returncode != 0:
            # older git without --no-write-fetch-head
            run([a for a in args if a != "--no-write-fetch-head"], cwd=dst.path)
        self._drop_fetch_head(dst)
        log(f"[{dst.key}] fetched objects from {src.key}")

    def _drop_fetch_head(self, dst: Side):
        try:
            fh = git_path(dst.path, "FETCH_HEAD")
            if fh.exists():
                fh.unlink()
        except (GitError, OSError):
            pass

    def _replay_queue(self, direction, src: Side, dst: Side, queue: list, log) -> dict:
        replayed = skipped = 0
        for i, p in enumerate(queue):
            head_before = head_sha(dst.path)
            outcome = self._apply(dst, p)
            if outcome == "conflict":
                store.save_pending(
                    self.pair_id,
                    {
                        "direction": direction,
                        "current": p.to_dict(),
                        "queue": [q.to_dict() for q in queue[i + 1 :]],
                        "head_before": head_before,
                        "time": _now(),
                    },
                )
                files = unmerged_files(dst.path)
                log(f"CONFLICT while applying {p.commit.sha[:8]} \"{p.commit.subject}\" into {dst.key}")
                for f in files:
                    log(f"    unresolved: {f}")
                log(f"Resolve the files in {dst.path}, stage them (git add), then Continue. Or Abort.")
                return {"status": "conflict", "replayed": replayed, "skipped": skipped, "files": files, "remaining": len(queue) - i - 1}
            if outcome == "empty":
                self._record(direction, p, None)
                skipped += 1
                log(f"  skip     {p.commit.sha[:8]} (no changes) {p.commit.subject}")
            else:
                new_sha = self._commit(dst, p)
                self._record(direction, p, new_sha)
                replayed += 1
                log(f"  {p.action:<8} {p.commit.sha[:8]} -> {new_sha[:8]}  {p.new_name} <{p.new_email}>  {p.commit.subject}")
        self._finish(dst, log)
        log(f"Done: {replayed} replayed, {skipped} skipped.")
        return {"status": "ok", "replayed": replayed, "skipped": skipped}

    def _apply(self, dst: Side, p: Planned) -> str:
        """Stage the commit's changes in dst. Returns applied | empty | conflict."""
        c = p.commit
        if head_sha(dst.path) is None:
            run(["read-tree", "--reset", "-u", f"{c.sha}^{{tree}}"], cwd=dst.path)
            return "applied" if staged_nonempty(dst.path) else "empty"
        args = ["cherry-pick", "--no-commit"]
        if c.is_merge:
            args += ["-m", "1"]
        args.append(c.sha)
        r = run(args, cwd=dst.path, check=False)
        if r.returncode != 0:
            if unmerged_files(dst.path):
                return "conflict"
            text = (r.stderr or "") + (r.stdout or "")
            if "empty" in text or "nothing to commit" in text or "nothing added" in text:
                run(["cherry-pick", "--quit"], cwd=dst.path, check=False)
                run(["reset", "--quiet", "--hard"], cwd=dst.path)
                return "empty"
            raise GitError(args, r.returncode, r.stdout, r.stderr)
        return "applied" if staged_nonempty(dst.path) else "empty"

    def _commit(self, dst: Side, p: Planned) -> str:
        c = p.commit
        # make sure git does not reuse the author from CHERRY_PICK_HEAD
        run(["cherry-pick", "--quit"], cwd=dst.path, check=False)
        try:
            cp = git_path(dst.path, "CHERRY_PICK_HEAD")
            if cp.exists():
                cp.unlink()
        except (GitError, OSError):
            pass
        env = {
            "GIT_AUTHOR_NAME": p.new_name,
            "GIT_AUTHOR_EMAIL": p.new_email,
            "GIT_AUTHOR_DATE": c.author_date,
            "GIT_COMMITTER_NAME": p.committer_name,
            "GIT_COMMITTER_EMAIL": p.committer_email,
            "GIT_COMMITTER_DATE": c.committer_date,
        }
        run(
            [
                "-c", "commit.gpgsign=false",
                "-c", f"user.name={p.committer_name}",
                "-c", f"user.email={p.committer_email}",
                "commit", "--quiet", "--no-verify", "--no-gpg-sign", "--allow-empty-message",
                f"--author={p.new_name} <{p.new_email}>", f"--date={c.author_date}", "-F", "-",
            ],
            cwd=dst.path,
            env=env,
            input=p.message + "\n",
        )
        return head_sha(dst.path)

    def _record(self, direction: str, p: Planned, dst_sha):
        c = p.commit
        if direction == TO_MIRROR:
            entry = {"original": c.sha, "mirror": dst_sha}
            self.mapped_original.add(c.sha)
            if dst_sha:
                self.mapped_mirror.add(dst_sha)
        else:
            entry = {"original": dst_sha, "mirror": c.sha}
            self.mapped_mirror.add(c.sha)
            if dst_sha:
                self.mapped_original.add(dst_sha)
        entry.update(
            {
                "direction": direction,
                "action": p.action if dst_sha else "skipped",
                "src_author": f"{c.author_name} <{c.author_email}>",
                "dst_author": f"{p.new_name} <{p.new_email}>",
                "subject": c.subject,
                "time": _now(),
            }
        )
        self.map["entries"].append(entry)
        store.save_map(self.pair_id, self.map)

    def _finish(self, dst: Side, log):
        self._drop_fetch_head(dst)
        if self.pair["scrub"]:
            r = run(["gc", "--prune=now", "--quiet"], cwd=dst.path, check=False)
            if r.returncode == 0:
                log(f"[{dst.key}] scrubbed unreachable objects (git gc --prune=now)")
            else:
                log(f"[{dst.key}] warning: gc failed: {r.stderr.strip()}")

    # -- conflict handling

    def continue_sync(self, log=print) -> dict:
        pending = store.load_pending(self.pair_id)
        if not pending:
            raise SyncError("No paused sync to continue.")
        direction = pending["direction"]
        src, dst = self.sides(direction)
        files = unmerged_files(dst.path)
        if files:
            raise SyncError("Still unresolved: " + ", ".join(files) + ". Fix them and run `git add` on each, then continue.")
        p = Planned.from_dict(pending["current"])
        if staged_nonempty(dst.path):
            new_sha = self._commit(dst, p)
            self._record(direction, p, new_sha)
            log(f"  {p.action:<8} {p.commit.sha[:8]} -> {new_sha[:8]}  (resolved)  {p.commit.subject}")
            replayed, skipped = 1, 0
        else:
            run(["cherry-pick", "--quit"], cwd=dst.path, check=False)
            self._record(direction, p, None)
            log(f"  skip     {p.commit.sha[:8]} (resolved to no changes) {p.commit.subject}")
            replayed, skipped = 0, 1
        store.clear_pending(self.pair_id)
        queue = [Planned.from_dict(q) for q in pending["queue"]]
        res = self._replay_queue(direction, src, dst, queue, log)
        res["replayed"] += replayed
        res["skipped"] += skipped
        return res

    def abort_sync(self, log=print) -> dict:
        pending = store.load_pending(self.pair_id)
        if not pending:
            raise SyncError("No paused sync to abort.")
        src, dst = self.sides(pending["direction"])
        run(["cherry-pick", "--quit"], cwd=dst.path, check=False)
        if pending.get("head_before"):
            run(["reset", "--quiet", "--hard", pending["head_before"]], cwd=dst.path)
        else:
            run(["reset", "--quiet", "--hard"], cwd=dst.path, check=False)
        store.clear_pending(self.pair_id)
        self._drop_fetch_head(dst)
        log(f"Aborted. {dst.key} reset to {(pending.get('head_before') or 'HEAD')[:8]}; commits replayed before the conflict were kept.")
        return {"status": "aborted"}

    # -- repo setup / identity

    def setup(self, key: str, log=print) -> dict:
        s = self.side(key)
        if not s.path:
            raise SyncError(f"{key} path is empty.")
        p = Path(s.path)
        if not p.exists():
            p.mkdir(parents=True)
            log(f"[{key}] created {p}")
        if not is_repo(p):
            run(["init", "--quiet"], cwd=p)
            run(["symbolic-ref", "HEAD", f"refs/heads/{s.branch}"], cwd=p)
            log(f"[{key}] initialised empty repository on branch {s.branch}")
        self.apply_identity(key, log)
        return {"status": "ok"}

    def apply_identity(self, key: str, log=print) -> dict:
        s = self.side(key)
        if not is_repo(s.path):
            raise SyncError(f"{key} is not a git repository: {s.path}")
        prof = s.profile
        cfg = lambda *a: run(["config", "--local", *a], cwd=s.path)
        cfg("user.name", prof["name"])
        cfg("user.email", prof["email"])
        log(f"[{key}] user.name={prof['name']!r} user.email={prof['email']!r}")
        if prof["disable_signing"]:
            cfg("commit.gpgsign", "false")
            cfg("tag.gpgsign", "false")
            log(f"[{key}] commit signing disabled for this repo")
        else:
            run(["config", "--local", "--unset", "commit.gpgsign"], cwd=s.path, check=False)
            run(["config", "--local", "--unset", "tag.gpgsign"], cwd=s.path, check=False)
        # reset any auth we may have set before
        run(["config", "--local", "--unset-all", "credential.helper"], cwd=s.path, check=False)
        run(["config", "--local", "--unset", "core.sshCommand"], cwd=s.path, check=False)
        if prof["auth_type"] == "https":
            if not prof["token"]:
                log(f"[{key}] warning: profile has no token; skipping credential setup")
            else:
                from urllib.parse import quote
                cred = store.cred_path(prof["id"])
                host = prof["host"] or "github.com"
                user = prof["username"] or prof["name"] or "git"
                store.write_secret_file(cred, f"https://{quote(user, safe='')}:{quote(prof['token'], safe='')}@{host}\n")
                cred_str = str(cred).replace("\\", "/")
                cfg("--add", "credential.helper", "")  # empty entry resets the global helper list
                cfg("--add", "credential.helper", f'store --file="{cred_str}"')
                log(f"[{key}] HTTPS credentials for {host} stored in {cred} (repo-local helper; global helpers bypassed)")
        elif prof["auth_type"] == "ssh":
            if prof["ssh_key"]:
                key_str = str(Path(prof["ssh_key"]).expanduser()).replace("\\", "/")
                cfg("core.sshCommand", f'ssh -i "{key_str}" -o IdentitiesOnly=yes')
                log(f"[{key}] core.sshCommand set to use {key_str}")
            else:
                log(f"[{key}] warning: profile has no SSH key path; skipping")
        if s.remote:
            if run(["remote", "get-url", "origin"], cwd=s.path, check=False).returncode == 0:
                run(["remote", "set-url", "origin", s.remote], cwd=s.path)
            else:
                run(["remote", "add", "origin", s.remote], cwd=s.path)
            log(f"[{key}] remote origin = {s.remote}")
        return {"status": "ok"}

    def push(self, key: str, log=print) -> dict:
        s = self.side(key)
        if not is_repo(s.path):
            raise SyncError(f"{key} is not a git repository: {s.path}")
        r = run(["push", "-u", "origin", f"{s.branch}:{s.branch}"], cwd=s.path, check=False)
        for line in (r.stderr + r.stdout).splitlines():
            log(f"[{key}] {line}")
        if r.returncode != 0:
            raise SyncError(f"push failed (see log). If it asked for credentials, apply the identity again or check the token.")
        return {"status": "ok"}

    def pull(self, key: str, log=print) -> dict:
        s = self.side(key)
        if not is_repo(s.path):
            raise SyncError(f"{key} is not a git repository: {s.path}")
        if not is_clean(s.path):
            raise SyncError(f"{key} has uncommitted changes.")
        self._checkout(s, log)
        r = run(["pull", "--ff-only", "origin", s.branch], cwd=s.path, check=False)
        for line in (r.stderr + r.stdout).splitlines():
            log(f"[{key}] {line}")
        if r.returncode != 0:
            raise SyncError("pull failed (see log). Only fast-forward pulls are done automatically; resolve manually otherwise.")
        return {"status": "ok"}
