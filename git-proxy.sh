#!/usr/bin/env bash
# Launch git-proxy on macOS/Linux. Opens the GUI when run without arguments.
set -e
root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
command -v git >/dev/null 2>&1 || { echo "git not found on PATH" >&2; exit 1; }
if command -v python3 >/dev/null 2>&1; then py=python3; elif command -v python >/dev/null 2>&1; then py=python; else
  echo "Python 3 not found" >&2; exit 1; fi
exec "$py" "$root/git-proxy.py" "$@"
