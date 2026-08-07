"""No-Qt/no-macOS import gate for the headless stack (ARCH-001)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

FORBIDDEN = {"PySide6", "PyQt6", "PyQt5", "AppKit", "Foundation", "keyring"}
FORBIDDEN_MODULE_PREFIXES = ("PySide6", "PyQt", "AppKit", "Foundation", "keyring")
FORBIDDEN_SOURCE_MARKERS = (
    "import PySide6",
    "from PySide6",
    "import AppKit",
    "from AppKit",
    "import Foundation",
    "from Foundation",
    "import keyring",
    "from keyring",
    "import objc",
    "stock_watcher.ui",
)

SERVICE_MODULES = (
    "stock_watcher.services.stockwatcher_service",
    "stock_watcher.services.command_service",
    "stock_watcher.services.event_outbox",
    "stock_watcher.services.secret_service",
    "stock_watcher.services.worker_lease",
    "stock_watcher.services.public_state",
    "stock_watcher.server.web",
    "stock_watcher.server.worker",
    "stock_watcher.server.api",
    "stock_watcher.server.auth",
    "stock_watcher.server.admin_cli",
    "stock_watcher.server.healthcheck",
    "stock_watcher.storage.web",
)


def test_headless_modules_import_without_qt_or_macos() -> None:
    """Import every server/service module in a subprocess and assert the
    forbidden frameworks never enter sys.modules."""
    script = """
import sys
modules = sys.argv[1:]
for name in modules:
    __import__(name)
loaded = set(sys.modules)
for name in sorted(loaded):
    root = name.split(".")[0]
    if root in {"PySide6", "PyQt6", "PyQt5", "AppKit", "Foundation", "keyring"}:
        print(f"FORBIDDEN_LOADED:{name}")
        raise SystemExit(1)
print("NO_FORBIDDEN_MODULES")
"""
    result = subprocess.run(
        [sys.executable, "-c", script, *SERVICE_MODULES],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "NO_FORBIDDEN_MODULES" in result.stdout


def test_static_scan_no_ui_or_macos_imports() -> None:
    root = Path(__file__).resolve().parents[1] / "src" / "stock_watcher"
    scanned = 0
    for path in (root / "services").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for marker in FORBIDDEN_SOURCE_MARKERS:
            assert marker not in text, f"{path}: contains {marker!r}"
        scanned += 1
    for path in (root / "server").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for marker in FORBIDDEN_SOURCE_MARKERS:
            assert marker not in text, f"{path}: contains {marker!r}"
        scanned += 1
    assert scanned >= 17


def test_worker_imports_do_not_pull_desktop_session() -> None:
    script = """
import sys
from stock_watcher.server import worker
assert "stock_watcher.ui" not in sys.modules
assert "stock_watcher.ui.tushare_v1_session" not in sys.modules
print("WORKER_HEADLESS_OK")
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    assert result.returncode == 0, result.stdout + result.stderr
