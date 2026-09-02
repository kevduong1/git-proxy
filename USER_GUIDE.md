# git-proxy User Guide

This guide walks through setting up and using git-proxy day to day. For the short version and the
identity rules table, see [README.md](README.md).

## 1. What the tool does

You have a repo tied to your personal git account. You want to share it with friends under a different
identity (an alias account), and later pull their work back, without their names or your alias showing up
in your personal repo's history.

git-proxy keeps **two local clones**:

- **Original**: your personal repo, committed as you, pushed to your personal remote (if any).
- **Mirror**: the shared copy, committed as the alias, pushed to the alias account's remote.

You never fetch or push between them directly. Instead you press **Sync**, which replays the new commits
from one side into the other and rewrites the author as it goes:

| direction | commit authored by | becomes |
|---|---|---|
| original → mirror | you | alias |
| original → mirror | anyone else | unchanged |
| mirror → original | alias | you |
| mirror → original | friends | `guest` |

Commit messages, author dates and committer dates are preserved. Your name and email inside messages
(e.g. `Signed-off-by:` lines) are swapped to the alias, and trailers such as `Co-authored-by: Copilot`
are removed from your commits. Friends' commits are never edited.

Every replayed commit is recorded in a mapping so it is never replayed twice. That is what makes the
round trip safe: a friend's commit lands in your original as `guest`, and when you sync back, the mirror
keeps the friend's real commit instead of getting a `guest` copy.

## 2. Install

Requirements: git 2.29 or newer, Python 3.9 or newer. No packages to install.

```sh
git clone https://github.com/kevduong1/git-proxy
cd git-proxy
```

Windows: install [Git for Windows](https://git-scm.com/download/win) and
[Python](https://www.python.org/downloads/) (tick *Add python.exe to PATH*).

## 3. Start the GUI

macOS / Linux:

```sh
./git-proxy.sh
```

Windows PowerShell:

```powershell
.\git-proxy.ps1
# if scripts are blocked on your machine:
powershell -ExecutionPolicy Bypass -File .\git-proxy.ps1
```

A browser tab opens on a local page (127.0.0.1, random port). The terminal shows the URL if it doesn't.
Leave the terminal open while you use the GUI. Ctrl+C stops it.

All state is stored in `~/.git-proxy` (`C:\Users\you\.git-proxy` on Windows). Set the `GIT_PROXY_HOME`
environment variable to move it.

## 4. One-time setup

### 4.1 Create two identity profiles

Sidebar → **Identity profiles → + New**.

**Personal profile**
- Label: `Personal`
- Commit name / email: exactly what your personal commits use now (`git log -1 --format='%an <%ae>'`).
- Auth type: `None` is fine if your personal pushes already work.

**Alias profile**
- Label: `Alias`
- Commit name / email: the alias. For GitHub use the account's noreply address
  (`12345+alias@users.noreply.github.com`) so no real email is exposed.
- Auth type:
  - **HTTPS + token**: host (`github.com`), the alias username, and a personal access token created
    while logged in as the alias. The token is written to a file under `~/.git-proxy/creds/` and wired
    into the mirror repo with a repo-local credential helper, so your global keychain / Windows
    Credential Manager is bypassed for that repo only.
  - **SSH key**: path to a private key that is registered on the alias account
    (e.g. `~/.ssh/id_ed25519_alias`). The repo gets `core.sshCommand` with `IdentitiesOnly=yes`.
- Tick **Disable commit/tag signing**. This stops your personal GPG/SSH signing key from ever
  signing a mirror commit.

### 4.2 Create the repo pair

Sidebar → **Repo pairs → + New**.

- **Original path**: your existing repo. **Profile**: Personal. **Branch**: usually `main`.
- **Mirror path**: a folder that does not exist yet, e.g. `~/repos/project-mirror`. **Profile**: Alias.
  **Remote URL**: the alias account's empty repo, e.g. `https://github.com/alias/project.git`
  (or the `git@github.com:alias/project.git` form for SSH).
- **Guest name / email**: how friends appear in your original. Default `guest`.
- **Extra emails that count as "me"**: any other addresses you have committed with in the past
  (old work email, etc.). Commits by those get re-authored as the alias too.
- **Trailers to remove**: `Co-authored-by` by default.
- Leave **rewrite messages** and **scrub** on.

Save. The pair opens in the main view with two cards.

### 4.3 Initialise the mirror

On the **Mirror** card press **Setup repo**. It creates the folder, runs `git init`, sets the branch,
applies the alias name/email, credentials, signing off, and the remote. The card's *repo ident* row should
now show a green *identity applied* pill.

Optional: press **Apply identity** on the Original card to pin your personal identity in that repo too.

### 4.4 First sync and push

1. Press **Preview →** on the Original card. The table lists every commit and shows
   `Kevin <you>` → `alias <alias>` with the action `rewrite`. Check there is nothing surprising.
2. Press **Sync → mirror**. The log shows each commit being replayed.
3. Press **Push** on the Mirror card. It pushes `main` to the alias remote using the alias credentials.

Before sharing, double check the mirror yourself:

```sh
cd ~/repos/project-mirror
git log --format='%an <%ae> | %cn <%ce> | %s'     # only the alias should appear
git grep -i "your name" $(git rev-list --all)      # anything identifying inside files?
```

The scrub step already removed the personal commit objects from the mirror's object store, but file
*contents* (LICENSE, package.json author, comments) are yours to check.

## 5. Everyday workflow

### Getting friends' work into your original

1. **Pull** on the Mirror card (fast-forward only).
2. **Preview →** on the Mirror card to see what will come across. Friends show as `guest`,
   anything you committed directly in the mirror as the alias shows as `restore`.
3. **← Sync to original**.

### Sending your work to friends

1. Commit in your original as usual.
2. **Preview →** on the Original card. Only your new commits appear; the `guest` commits that came from
   the mirror are already mapped and are not sent back.
3. **Sync → mirror**, then **Push** on the Mirror card.

### Both sides have new commits

Do mirror → original first, then original → mirror. The replay works like a rebase, so the commit order
differs slightly between the two repos, but the resulting file trees are identical.

### Committing directly in the mirror

That's fine. Because the mirror's repo-local config uses the alias identity, commits there are authored as
the alias. When you sync to the original they are restored to your personal identity.

## 6. Conflicts

If a replayed commit does not apply cleanly (you and a friend edited the same lines), the sync pauses
and an orange banner appears naming the commit and the unresolved files.

1. Open the destination repo (the banner tells you which path) in your editor.
2. Resolve the conflict markers, then `git add` each file. Do **not** run `git commit` yourself.
3. Press **Continue**. The commit is created with the planned identity and the rest of the queue proceeds.

**Abort** resets the destination to the last successfully replayed commit and forgets the paused queue.
Commits replayed before the conflict are kept and stay mapped, so the next sync starts from the conflict.

## 7. CLI

Everything the buttons do is available from the command line (handy in scripts):

```sh
./git-proxy.sh pairs                          # list pairs with their ids
./git-proxy.sh profiles
./git-proxy.sh status  <pair>                 # JSON status of both sides
./git-proxy.sh plan    <pair> to-mirror       # preview (to-mirror | to-original)
./git-proxy.sh sync    <pair> to-original     # exit code 2 = paused on a conflict
./git-proxy.sh continue <pair>
./git-proxy.sh abort   <pair>
./git-proxy.sh setup   <pair> mirror          # init if needed + apply identity
./git-proxy.sh apply   <pair> original        # (re)apply identity/credentials
./git-proxy.sh pull    <pair> mirror
./git-proxy.sh push    <pair> mirror
./git-proxy.sh history <pair>
```

`<pair>` is the pair's label or id. On Windows use `.\git-proxy.ps1` with the same arguments.

## 8. Rules of the road

- **Never** `git fetch`, `git pull` or `git remote add` between the original and the mirror by hand, and
  never push the original to the alias remote. Always go through Sync. Doing it by hand would copy your
  real commits into the shared repo.
- Both repos must be on their configured branch with no uncommitted changes when you sync.
- Only one branch per pair is synced. Create another pair for another branch.
- Rewriting history in either repo (rebase, amend, force-push) after it has been synced makes the mapping
  stale for those commits. Avoid it, or accept that the rewritten commits will be replayed again.
- Tokens are stored in plain text in `~/.git-proxy/creds/`. Treat that directory like `~/.ssh`.
- Keep the mirror folder out of anything that syncs to your personal cloud storage if it matters to you.

## 9. Troubleshooting

| Symptom | What to do |
|---|---|
| *identity differs* pill on a card | Press **Apply identity** (or **Setup repo**). |
| Push asks for a password / fails with 403 | The token is wrong or expired. Edit the Alias profile, paste a new token, press **Apply identity** on the mirror. |
| Push uses the wrong account on Windows | Global Credential Manager is being consulted. Press **Apply identity** again; it inserts an empty helper entry that disables global helpers for that repo. Check with `git config --local --get-all credential.helper`. |
| `has uncommitted changes` | Commit or stash in that repo, then retry. |
| Preview shows a friend's commit as `rewrite` | Their email or name matches your personal profile or your extra emails. Fix the profile/pair settings. |
| Preview shows one of your commits as `keep` | That commit uses an email not in your profile. Add it under *Extra emails that count as "me"*. |
| Sync says nothing to do but the repos differ | Someone rewrote history. Compare `git log` on both sides; in the worst case delete the pair (mapping only, repos untouched) and rebuild the mirror from scratch. |
| Windows: "running scripts is disabled" | `powershell -ExecutionPolicy Bypass -File .\git-proxy.ps1` |

## 10. Running the tests

```sh
python3 -m unittest discover -s tests -v
```

The tests create throwaway repos in a temp directory and never touch `~/.git-proxy`.
