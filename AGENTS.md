# AGENTS.md

Guidance for AI coding agents (and humans) working in or with this repository.

## What this project is

git-proxy replays commits between two local git repos, an **original** (the user's personal repo) and a
**mirror** (a copy shared with other people), rewriting authorship on the way. The user's commits become an
alias identity in the mirror; other people's commits become a `guest` identity in the original. A per-pair
mapping file records every replayed commit so nothing is replayed twice in either direction.

Pure Python 3.9+ standard library, plus the `git` executable. No dependencies, no build step.

## Layout

```
git-proxy.py            entry point (adds repo root to sys.path, calls gitproxy.cli.main)
git-proxy.sh / .ps1     launchers for macOS/Linux and Windows PowerShell
gitproxy/
  gitcmd.py             run(): subprocess wrapper around git, never prompts (GIT_TERMINAL_PROMPT=0)
  store.py              JSON state under ~/.git-proxy (or $GIT_PROXY_HOME): config, maps, pending, creds
  sync.py               Engine: plan / sync / continue / abort / setup / apply_identity / push / pull
  cli.py                argparse CLI; no subcommand => GUI
  server.py             ThreadingHTTPServer on 127.0.0.1, JSON API, per-session token header
  static/index.html     single-file GUI (vanilla JS, no framework, no external assets)
tests/test_sync.py      end-to-end tests on real temp repos (unittest)
README.md               overview and rules table
USER_GUIDE.md           step-by-step usage
```

## Key concepts in `sync.py`

- Directions are the constants `TO_MIRROR` (`"to-mirror"`) and `TO_ORIGINAL` (`"to-original"`).
- `Engine(pair_id)` loads config + mapping. `sides(direction)` returns `(src, dst)` `Side` objects.
- `plan(direction)` lists source commits (`git log --reverse --topo-order`) whose sha is not in the
  mapping and runs `decide()` on each, producing a `Planned` with an `action`:
  `rewrite` (mine → alias), `keep` (verbatim), `restore` (alias → me), `guest` (others → guest).
- `sync()` fetches the source's objects into the destination (`git fetch <path> <branch>`, no remote
  is added, FETCH_HEAD is removed), then for each planned commit: `cherry-pick --no-commit`
  (merges use `-m 1`), then `git commit` with explicit `--author`, `--date`, and `GIT_COMMITTER_*` env,
  `--no-verify --no-gpg-sign`. Empty results are recorded as `skipped` with a null destination sha.
- On conflict the queue is persisted (`store.save_pending`) and `sync()` returns `status: "conflict"`.
  `continue_sync()` commits the staged resolution and drains the queue; `abort_sync()` hard-resets to the
  head recorded before the failing commit.
- `_finish()` runs `git gc --prune=now` when the pair's `scrub` flag is on so source commit objects
  do not remain in the destination's object store.
- `apply_identity()` sets repo-local `user.*`, disables signing if the profile says so, and configures
  either a repo-local `credential.helper` (an empty entry first, which disables global helpers, then
  `store --file=...`) or `core.sshCommand`.

## Invariants: do not break these

1. **Never leak the personal identity into the mirror.** Every commit created in the mirror must have
   alias author *and* committer, and the mirror's object store must not retain the original commit
   objects after a sync. `test_round_trip` checks both; keep it passing.
2. **Never replay a mapped commit.** The mapping is the source of truth for "already on the other side".
   Any new code path that creates a commit in a destination must call `_record()`.
3. **Friends' commits are never edited** (message or authorship) when going original → mirror.
   Trailer stripping and name/email substitution apply only to `rewrite` and `restore` actions.
4. **Never add the other repo as a remote, and never push between them.** Object transfer is fetch-by-path
   only, and FETCH_HEAD is deleted afterwards.
5. **Never prompt.** All git calls go through `gitcmd.run`, which sets `GIT_TERMINAL_PROMPT=0`.
   Errors surface as `GitError` / `SyncError` and are shown in the GUI log.
6. **Stay dependency-free and cross-platform.** Standard library only. Use `pathlib`, forward slashes in
   values written to git config, and no shell=True.

## Running things

```sh
python3 -m unittest discover -s tests -v      # tests (isolated: GIT_PROXY_HOME + empty global gitconfig)
./git-proxy.sh gui --no-browser --port 8642   # GUI without opening a browser
GIT_PROXY_HOME=/tmp/gp ./git-proxy.sh ...     # use a throwaway state dir while developing
```

Do not run the GUI or CLI against the real `~/.git-proxy` while developing; point `GIT_PROXY_HOME`
at a scratch directory. Do not commit, push or create remotes on the user's behalf unless asked.

## Adding features

- A new per-pair option: add the default to `PAIR_DEFAULTS` in `store.py` (and parse it in
  `normalize_pair` if it comes from a form as a string), read it from `self.pair[...]` in `sync.py`,
  add the field to `editPair()` in `index.html` and to the default object there, document it in
  README/USER_GUIDE, and add a test.
- A new per-profile option: same pattern with `PROFILE_DEFAULTS`, `apply_identity()`, `editProfile()`.
  Secrets must go through `store.write_secret_file` and be masked in `server._masked_profiles`.
- A new action: add a method on `Engine` taking `(…, log)` where `log` is a callable receiving strings,
  route it in `server.Api.pairs_action` and `cli.main`, and add a button in `index.html` that calls
  `act('<name>', side?, direction?)`.
- The HTTP API is `GET /api/state`, `POST /api/profiles/save|delete`, `POST /api/pairs/save|delete`,
  `GET /api/pairs/status|plan|history?id=…`, `POST /api/pairs/action {id, action, side?, direction?}`.
  Every request needs the `X-GitProxy-Token` header whose value is injected into the page.

## Testing guidance

Tests must create their own repos in a `TemporaryDirectory`, set `GIT_PROXY_HOME`, `GIT_CONFIG_GLOBAL`
(to an empty file) and `GIT_CONFIG_NOSYSTEM=1`, and pass `-c commit.gpgsign=false` on test commits so a
developer's global signing config cannot interfere. Assert on `git log --format=%an|%ae|%cn|%ce|%s`
rather than on shas, which change with dates.
