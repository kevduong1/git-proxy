"""Thin subprocess wrapper around the git executable."""
from __future__ import annotations

import os
import shutil
import subprocess

GIT = shutil.which("git") or "git"


class GitError(Exception):
    def __init__(self, args, code, out, err):
        self.args_ = list(args)
        self.code = code
        self.out = out
        self.err = err
        super().__init__(f"git {' '.join(self.args_)} failed ({code}): {(err or out).strip()}")


def run(args, cwd=None, env=None, check=True, input=None):
    """Run git with the given args. Never prompts for credentials/passwords."""
    full_env = os.environ.copy()
    full_env.setdefault("GIT_TERMINAL_PROMPT", "0")
    full_env.setdefault("LC_ALL", "C.UTF-8")
    if env:
        full_env.update(env)
    p = subprocess.run(
        [GIT] + [str(a) for a in args],
        cwd=str(cwd) if cwd else None,
        env=full_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        input=input,
    )
    if check and p.returncode != 0:
        raise GitError(args, p.returncode, p.stdout, p.stderr)
    return p
