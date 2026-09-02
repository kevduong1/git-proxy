"""Persistent state: profiles, pairs, commit mappings, paused syncs, credential files.

Everything lives under ~/.git-proxy (override with the GIT_PROXY_HOME env var).
"""
from __future__ import annotations

import json
import os
import secrets
import stat
from pathlib import Path


def home() -> Path:
    return Path(os.environ.get("GIT_PROXY_HOME") or (Path.home() / ".git-proxy"))


def _ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        try:
            os.chmod(p, stat.S_IRWXU)
        except OSError:
            pass


def _read_json(path: Path, default):
    if not path.exists():
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: Path, data):
    _ensure_dir(path.parent)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)
    if os.name != "nt":
        try:
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass


def new_id() -> str:
    return secrets.token_hex(4)


# ---------------------------------------------------------------- config

PROFILE_DEFAULTS = {
    "id": "",
    "label": "",
    "name": "",
    "email": "",
    "auth_type": "none",  # none | https | ssh
    "username": "",
    "token": "",
    "host": "github.com",
    "ssh_key": "",
    "disable_signing": False,
}

PAIR_DEFAULTS = {
    "id": "",
    "label": "",
    "original_path": "",
    "original_profile": "",
    "original_branch": "main",
    "original_remote": "",
    "mirror_path": "",
    "mirror_profile": "",
    "mirror_branch": "main",
    "mirror_remote": "",
    "guest_name": "guest",
    "guest_email": "guest@users.noreply.local",
    "my_emails": [],
    "scrub": True,
    "rewrite_messages": True,
    "strip_trailers": ["Co-authored-by"],
}


def normalize_profile(data: dict) -> dict:
    p = dict(PROFILE_DEFAULTS)
    for k in PROFILE_DEFAULTS:
        if k in data and data[k] is not None:
            p[k] = data[k]
    p["disable_signing"] = bool(p["disable_signing"])
    if p["auth_type"] not in ("none", "https", "ssh"):
        p["auth_type"] = "none"
    if not p["id"]:
        p["id"] = new_id()
    if not p["label"]:
        p["label"] = p["name"] or p["id"]
    return p


def normalize_pair(data: dict) -> dict:
    p = dict(PAIR_DEFAULTS)
    for k in PAIR_DEFAULTS:
        if k in data and data[k] is not None:
            p[k] = data[k]
    if isinstance(p["my_emails"], str):
        p["my_emails"] = [e.strip() for e in p["my_emails"].replace(";", ",").split(",") if e.strip()]
    if isinstance(p["strip_trailers"], str):
        p["strip_trailers"] = [t.strip().rstrip(":") for t in p["strip_trailers"].replace(";", ",").split(",") if t.strip()]
    p["scrub"] = bool(p["scrub"])
    p["rewrite_messages"] = bool(p["rewrite_messages"])
    p["original_branch"] = p["original_branch"] or "main"
    p["mirror_branch"] = p["mirror_branch"] or "main"
    if not p["id"]:
        p["id"] = new_id()
    if not p["label"]:
        p["label"] = Path(p["original_path"]).name or p["id"]
    return p


def config_path() -> Path:
    return home() / "config.json"


def load_config() -> dict:
    cfg = _read_json(config_path(), {})
    cfg.setdefault("profiles", {})
    cfg.setdefault("pairs", {})
    return cfg


def save_config(cfg: dict):
    _write_json(config_path(), cfg)


# ---------------------------------------------------------------- per-pair state

def map_path(pair_id: str) -> Path:
    return home() / "pairs" / f"{pair_id}.map.json"


def load_map(pair_id: str) -> dict:
    m = _read_json(map_path(pair_id), {})
    m.setdefault("entries", [])
    return m


def save_map(pair_id: str, m: dict):
    _write_json(map_path(pair_id), m)


def pending_path(pair_id: str) -> Path:
    return home() / "pairs" / f"{pair_id}.pending.json"


def load_pending(pair_id: str):
    return _read_json(pending_path(pair_id), None)


def save_pending(pair_id: str, data: dict):
    _write_json(pending_path(pair_id), data)


def clear_pending(pair_id: str):
    try:
        pending_path(pair_id).unlink()
    except FileNotFoundError:
        pass


def delete_pair_state(pair_id: str):
    for p in (map_path(pair_id), pending_path(pair_id)):
        try:
            p.unlink()
        except FileNotFoundError:
            pass


# ---------------------------------------------------------------- credentials

def cred_path(profile_id: str) -> Path:
    return home() / "creds" / f"{profile_id}.git-credentials"


def write_secret_file(path: Path, text: str):
    _ensure_dir(path.parent)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    if os.name != "nt":
        try:
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass


def delete_cred(profile_id: str):
    try:
        cred_path(profile_id).unlink()
    except FileNotFoundError:
        pass
