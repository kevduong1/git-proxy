"""Local web GUI: a tiny JSON API over http.server plus a single HTML page."""
from __future__ import annotations

import json
import secrets
import threading
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import store
from .gitcmd import GitError
from .sync import Engine, SyncError

STATIC = Path(__file__).parent / "static"
_LOCK = threading.Lock()
_TOKEN = secrets.token_urlsafe(24)


def _masked_profiles(cfg):
    out = {}
    for pid, p in cfg["profiles"].items():
        p = store.normalize_profile(p)
        p["has_token"] = bool(p["token"])
        p["token"] = ""
        out[pid] = p
    return out


class Api:
    def state(self, q, body):
        cfg = store.load_config()
        return {
            "profiles": _masked_profiles(cfg),
            "pairs": {pid: store.normalize_pair(p) for pid, p in cfg["pairs"].items()},
            "home": str(store.home()),
        }

    def profiles_save(self, q, body):
        cfg = store.load_config()
        p = store.normalize_profile(body.get("profile", {}))
        old = cfg["profiles"].get(p["id"])
        if not p["token"] and old and not body.get("clear_token"):
            p["token"] = old.get("token", "")
        cfg["profiles"][p["id"]] = p
        store.save_config(cfg)
        return {"id": p["id"]}

    def profiles_delete(self, q, body):
        cfg = store.load_config()
        pid = body.get("id")
        cfg["profiles"].pop(pid, None)
        store.delete_cred(pid)
        store.save_config(cfg)
        return {}

    def pairs_save(self, q, body):
        cfg = store.load_config()
        p = store.normalize_pair(body.get("pair", {}))
        cfg["pairs"][p["id"]] = p
        store.save_config(cfg)
        return {"id": p["id"]}

    def pairs_delete(self, q, body):
        cfg = store.load_config()
        pid = body.get("id")
        cfg["pairs"].pop(pid, None)
        store.delete_pair_state(pid)
        store.save_config(cfg)
        return {}

    def pairs_status(self, q, body):
        return Engine(q["id"]).status()

    def pairs_plan(self, q, body):
        eng = Engine(q["id"])
        return {"plan": [p.summary() for p in eng.plan(q["direction"])]}

    def pairs_history(self, q, body):
        return {"history": Engine(q["id"]).history()}

    def pairs_action(self, q, body):
        log = []
        eng = Engine(body["id"])
        action = body.get("action")
        try:
            if action == "sync":
                res = eng.sync(body["direction"], log.append)
            elif action == "continue":
                res = eng.continue_sync(log.append)
            elif action == "abort":
                res = eng.abort_sync(log.append)
            elif action == "setup":
                res = eng.setup(body["side"], log.append)
            elif action == "apply":
                res = eng.apply_identity(body["side"], log.append)
            elif action == "push":
                res = eng.push(body["side"], log.append)
            elif action == "pull":
                res = eng.pull(body["side"], log.append)
            else:
                raise SyncError(f"unknown action {action}")
        except (SyncError, GitError) as e:
            return {"ok": False, "error": str(e), "log": log}
        return {"ok": True, "result": res, "log": log}


API = Api()
ROUTES = {
    "GET /api/state": API.state,
    "POST /api/profiles/save": API.profiles_save,
    "POST /api/profiles/delete": API.profiles_delete,
    "POST /api/pairs/save": API.pairs_save,
    "POST /api/pairs/delete": API.pairs_delete,
    "GET /api/pairs/status": API.pairs_status,
    "GET /api/pairs/plan": API.pairs_plan,
    "GET /api/pairs/history": API.pairs_history,
    "POST /api/pairs/action": API.pairs_action,
}


class Handler(BaseHTTPRequestHandler):
    server_version = "git-proxy"

    def log_message(self, fmt, *args):  # quiet
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _handle(self, method):
        u = urlparse(self.path)
        if method == "GET" and u.path in ("/", "/index.html"):
            html = (STATIC / "index.html").read_text(encoding="utf-8").replace("__TOKEN__", _TOKEN)
            return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
        if not u.path.startswith("/api/"):
            return self._send(404, {"error": "not found"})
        if self.headers.get("X-GitProxy-Token") != _TOKEN:
            return self._send(403, {"error": "bad token"})
        fn = ROUTES.get(f"{method} {u.path}")
        if not fn:
            return self._send(404, {"error": "no such route"})
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        body = {}
        if method == "POST":
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
        try:
            with _LOCK:
                res = fn(q, body)
            if isinstance(res, dict) and "ok" in res:
                return self._send(200, res)
            return self._send(200, {"ok": True, "result": res})
        except (SyncError, GitError) as e:
            return self._send(200, {"ok": False, "error": str(e)})
        except Exception as e:  # noqa
            traceback.print_exc()
            return self._send(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_OPTIONS(self):
        self._send(403, {"error": "no cors"})


def serve(port: int = 0, open_browser: bool = True) -> int:
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    print(f"git-proxy GUI: {url}")
    print(f"state directory: {store.home()}")
    print("Press Ctrl+C to stop.")
    if open_browser:
        threading.Timer(0.3, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0
