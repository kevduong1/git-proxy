# git-proxy

Share a repo with friends under a different git identity, without linking it to your personal account.

git-proxy keeps two local clones side by side, an **original** (yours) and a **mirror** (the copy you share),
and *replays* commits between them, rewriting authorship as they cross:

| direction | commit authored by | becomes |
|---|---|---|
| original → mirror | you (personal profile, or any extra "my emails") | **alias** profile |
| original → mirror | anyone else | unchanged |
| mirror → original | the alias | restored to **you** |
| mirror → original | anyone else (your friends) | **guest** identity |

Every replayed commit is remembered in a mapping (original sha ↔ mirror sha), so nothing is replayed twice
in either direction. That is what makes the round trip work: a friend's commit lands in your original as
`guest`, and when you sync back the mirror keeps the friend's real commit instead of getting a `guest` copy.

Commits are re-created with the same author date, committer date and message (with your name/email swapped
inside messages too, e.g. `Signed-off-by:` lines, and configurable trailers such as `Co-authored-by: Copilot`
removed), never signed, and hooks are skipped. After each sync the
mirror runs `git gc --prune=now` so the original commit objects never linger in its object store.

The mirror gets a repo-local `user.name`/`user.email`, a repo-local credential helper (HTTPS token) or
`core.sshCommand` (SSH key) for pushing, and commit signing disabled, so nothing global has to change.

See [USER_GUIDE.md](USER_GUIDE.md) for a step-by-step walkthrough and [AGENTS.md](AGENTS.md) for
contributor / AI-agent notes.

## Requirements

- git 2.29+
- Python 3.9+ (standard library only, no packages to install)
- Works on macOS, Linux and Windows (PowerShell)

## Run

```sh
./git-proxy.sh              # macOS / Linux: opens the GUI in your browser
```

```powershell
.\git-proxy.ps1             # Windows PowerShell
# if scripts are blocked:  powershell -ExecutionPolicy Bypass -File .\git-proxy.ps1
```

The GUI is a local web page served on `127.0.0.1` only (random port, per-session token).

## Workflow

1. **Profiles**: create two identity profiles: *Personal* (your real name/email) and *Alias*
   (alias name/email + the HTTPS token or SSH key of the alias account; tick *disable signing*).
2. **Pair**: create a repo pair: path of your original repo + Personal profile, path for the mirror
   (can be a folder that does not exist yet) + Alias profile, optional remote URL for the mirror.
3. Press **Setup repo** on the mirror card. It creates/initialises the folder and applies the alias identity
   and credentials. Press **Apply identity** on the original if you want its config pinned too.
4. **Preview →** shows exactly which commits would be replayed and as whom. **Sync → mirror** does it.
5. **Push** the mirror to the alias account's remote. Share that with friends.
6. Later: **Pull** the mirror, **← Sync to original** (friends become `guest`), work in your original,
   **Sync → mirror**, **Push**.

CLI equivalents:

```sh
./git-proxy.sh pairs
./git-proxy.sh plan   <pair> to-mirror
./git-proxy.sh sync   <pair> to-mirror        # or to-original
./git-proxy.sh setup  <pair> mirror
./git-proxy.sh push   <pair> mirror
./git-proxy.sh pull   <pair> mirror
./git-proxy.sh status <pair>
./git-proxy.sh history <pair>
```

## Conflicts

If a replayed commit conflicts (e.g. you and a friend edited the same line), the sync pauses. Fix the files
in the destination repo, `git add` them, and press **Continue**; the resolved commit is created with the
planned identity and the rest of the queue proceeds. **Abort** resets the destination to the last successfully
replayed commit and drops the paused queue (nothing already replayed is lost).

## Notes and caveats

- Sync is linear, like a rebase: commits are cherry-picked one by one onto the destination branch. Merge
  commits are applied against their first parent, which normally makes them empty (skipped) once both sides
  have been replayed. Conflict resolutions inside merge commits may need a manual resolve.
- Both repos should be on their configured branch with no uncommitted changes when syncing.
- Never `git fetch`/`git pull` directly between the two repos, and never push the original to the alias
  remote; that would leak the real commits. Always go through the sync.
- Tokens are stored in plain text under `~/.git-proxy/creds/` (mode 600 on macOS/Linux). Set `GIT_PROXY_HOME`
  to move the state directory.
- Anything that identifies you *inside* files (package.json author, LICENSE, comments) is not rewritten.

## Tests

```sh
python3 -m unittest discover -s tests -v
```
