#!/usr/bin/env python3
"""Entry point. Run with no arguments to open the GUI, or `--help` for the CLI."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gitproxy.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
