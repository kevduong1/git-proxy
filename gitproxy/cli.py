"""Command line interface. `git-proxy.py` with no arguments opens the GUI."""
from __future__ import annotations

import argparse
import json
import sys

from . import store
from .gitcmd import GitError
from .sync import DIRECTIONS, Engine, SyncError


def _resolve_pair(cfg, ident):
    if ident in cfg["pairs"]:
        return ident
    for pid, p in cfg["pairs"].items():
        if p.get("label") == ident:
            return pid
    sys.exit(f"Unknown pair '{ident}'. Known pairs: " + ", ".join(f"{p.get('label')} ({pid})" for pid, p in cfg["pairs"].items()))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="git-proxy", description="Replay commits between two repos under different identities.")
    sub = ap.add_subparsers(dest="cmd")

    g = sub.add_parser("gui", help="open the web GUI (default)")
    g.add_argument("--port", type=int, default=0, help="port to listen on (default: random free port)")
    g.add_argument("--no-browser", action="store_true")

    sub.add_parser("profiles", help="list identity profiles")
    sub.add_parser("pairs", help="list repo pairs")

    for name, help_ in (("status", "show status of a pair"), ("history", "show replayed commits")):
        s = sub.add_parser(name, help=help_)
        s.add_argument("pair")

    for name, help_ in (("plan", "preview what a sync would do"), ("sync", "replay commits")):
        s = sub.add_parser(name, help=help_)
        s.add_argument("pair")
        s.add_argument("direction", choices=DIRECTIONS)

    for name, help_ in (("continue", "continue a sync paused on a conflict"), ("abort", "abort a paused sync")):
        s = sub.add_parser(name, help=help_)
        s.add_argument("pair")

    for name, help_ in (
        ("setup", "create/init a side if needed and apply its identity"),
        ("apply", "apply the identity + credentials to a side"),
        ("push", "push a side to its origin"),
        ("pull", "fast-forward pull a side from its origin"),
    ):
        s = sub.add_parser(name, help=help_)
        s.add_argument("pair")
        s.add_argument("side", choices=("original", "mirror"))

    args = ap.parse_args(argv)
    if not args.cmd or args.cmd == "gui":
        from .server import serve

        return serve(port=getattr(args, "port", 0), open_browser=not getattr(args, "no_browser", False))

    cfg = store.load_config()
    if args.cmd == "profiles":
        for pid, p in cfg["profiles"].items():
            p = store.normalize_profile(p)
            print(f"{pid}  {p['label']:<20} {p['name']} <{p['email']}>  auth={p['auth_type']}")
        return 0
    if args.cmd == "pairs":
        for pid, p in cfg["pairs"].items():
            p = store.normalize_pair(p)
            print(f"{pid}  {p['label']:<20} {p['original_path']}  <->  {p['mirror_path']}")
        return 0

    pid = _resolve_pair(cfg, args.pair)
    try:
        eng = Engine(pid, cfg)
        if args.cmd == "status":
            print(json.dumps(eng.status(), indent=2))
        elif args.cmd == "history":
            for e in eng.history():
                print(f"{e['time']}  {e['direction']:<12} {e['action']:<8} {(e['original'] or '-')[:8]} <-> {(e['mirror'] or '-')[:8]}  {e['src_author']} => {e['dst_author']}  {e['subject']}")
        elif args.cmd == "plan":
            plan = eng.plan(args.direction)
            if not plan:
                print("Nothing to sync.")
            for p in plan:
                s = p.summary()
                print(f"{s['short']}  {s['action']:<8} {s['from']} => {s['to']}  {s['subject']}")
        elif args.cmd == "sync":
            res = eng.sync(args.direction)
            print(json.dumps(res))
            return 2 if res["status"] == "conflict" else 0
        elif args.cmd == "continue":
            res = eng.continue_sync()
            return 2 if res["status"] == "conflict" else 0
        elif args.cmd == "abort":
            eng.abort_sync()
        elif args.cmd == "setup":
            eng.setup(args.side)
        elif args.cmd == "apply":
            eng.apply_identity(args.side)
        elif args.cmd == "push":
            eng.push(args.side)
        elif args.cmd == "pull":
            eng.pull(args.side)
    except (SyncError, GitError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0
