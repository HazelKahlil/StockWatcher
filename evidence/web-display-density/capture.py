#!/usr/bin/env python3
"""Write display-density evidence. Fail with a non-zero status on layout errors."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))
from web_display_harness import run_geometry  # noqa: E402

OUT = Path(__file__).resolve().parent


def main() -> int:
    return run_geometry(out_dir=OUT, write_shots=True, prefix="round3")


if __name__ == "__main__":
    raise SystemExit(main())
