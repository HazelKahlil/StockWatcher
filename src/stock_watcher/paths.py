from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class RuntimePaths:
    root: Path
    data: Path
    logs: Path
    reports: Path
    database: Path

    def create(self) -> None:
        for directory in (self.root, self.data, self.logs, self.reports):
            directory.mkdir(parents=True, exist_ok=True)


def runtime_paths(app_name: str = "StockWatcher") -> RuntimePaths:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        root = base / app_name
        logs = root / "logs"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
        root = base / app_name
        logs = Path.home() / "Library" / "Logs" / app_name
    else:
        base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
        root = base / app_name
        logs = root / "logs"
    return RuntimePaths(
        root=root,
        data=root / "data",
        logs=logs,
        reports=root / "reports",
        database=root / "data" / "stock-watcher.sqlite3",
    )
