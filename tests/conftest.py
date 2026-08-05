"""Ensure tests import this checkout's src/, not whatever editable install
happens to be active in the interpreter (e.g. a sibling git worktree)."""

import sys
from pathlib import Path

_SRC = str(Path(__file__).resolve().parent.parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)
