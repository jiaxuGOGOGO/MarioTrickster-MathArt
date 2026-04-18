"""Compatibility entry point for ``python -m MarioTrickster``."""
from __future__ import annotations

import sys

from mathart.cli import main


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
