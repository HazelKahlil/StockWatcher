from __future__ import annotations

import os
import sys
from pathlib import Path

_UNKNOWN = "unknown"


def source_commit() -> str:
    """Return the immutable commit embedded by the packaging specification."""
    override = os.environ.get("STOCKWATCHER_SOURCE_COMMIT", "").strip()
    if override:
        return override
    candidates: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if isinstance(meipass, str):
        candidates.append(Path(meipass) / "stock_watcher" / "SOURCE_COMMIT")
    candidates.append(Path(__file__).resolve().parent / "SOURCE_COMMIT")
    for path in candidates:
        try:
            value = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if value:
            return value
    return _UNKNOWN
