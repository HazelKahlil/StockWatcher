# -*- mode: python ; coding: utf-8 -*-
import os
import subprocess
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

project_root = Path(SPECPATH).parent
icon_path = (
    project_root
    / "src"
    / "stock_watcher"
    / "ui"
    / "assets"
    / "stockwatcher.ico"
)
runtime_icon_path = (
    project_root
    / "src"
    / "stock_watcher"
    / "ui"
    / "assets"
    / "stockwatcher.png"
)


def _source_commit():
    configured = os.environ.get("STOCKWATCHER_SOURCE_COMMIT", "").strip()
    if configured:
        return configured
    try:
        return subprocess.check_output(
            ["git", "-C", str(project_root), "rev-parse", "HEAD"],
            text=True,
        ).strip()
    except Exception:
        return "unknown"


provenance_dir = project_root / "build" / "provenance"
provenance_dir.mkdir(parents=True, exist_ok=True)
source_commit_file = provenance_dir / "SOURCE_COMMIT"
source_commit_file.write_text(_source_commit() + "\n", encoding="utf-8")
datas = [
    (str(runtime_icon_path), "stock_watcher/ui/assets"),
    (str(source_commit_file), "stock_watcher"),
]
seed = os.environ.get("STOCKWATCHER_UNIVERSE_SEED_PATH", "").strip()
if seed and Path(seed).is_file():
    datas.append((str(Path(seed)), "stock_watcher/data"))

analysis = Analysis(
    [
        str(
            project_root
            / "packaging"
            / "windows"
            / "portable"
            / "stockwatcher_portable.py"
        )
    ],
    pathex=[str(project_root / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=[
        *collect_submodules("keyring.backends"),
        "tushare",
        "tushare.stock",
        "tushare.stock.cons",
        "tushare.stock.rtq",
        "stock_watcher.providers.tushare.capability_router",
        "stock_watcher.providers.tushare.fast_transport",
        "stock_watcher.providers.tushare.native_realtime_transport",
        "stock_watcher.providers.tushare.pro_proxy_transport",
        "stock_watcher.providers.tushare.provider",
        "stock_watcher.providers.tushare.super_transport",
        "stock_watcher.providers.tushare.unified_provider",
        "stock_watcher.providers.tdxquant",
        "stock_watcher.providers.tdxquant_preflight",
        "stock_watcher.providers.tdxquant_m0",
        "stock_watcher.runtime.data_health",
        "stock_watcher.runtime.automation",
        "stock_watcher.runtime.market_session",
        "stock_watcher.runtime.scan_coordinator",
        "stock_watcher.runtime.tushare_runtime",
        "stock_watcher.ui.app",
        "stock_watcher.ui.daily_summary",
        "stock_watcher.ui.data_source_settings",
        "stock_watcher.ui.tdx_session",
        "stock_watcher.ui.tushare_v1_session",
        "stock_watcher.ui.tushare_session",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tqcenter"],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="StockWatcher",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=str(icon_path),
    version=str(project_root / "packaging" / "windows" / "version_info.txt"),
)
collection = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=True,
    name="StockWatcher",
)
