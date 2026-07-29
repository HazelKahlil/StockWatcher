# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

project_root = Path(SPECPATH).parent

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
    datas=[],
    hiddenimports=[
        *collect_submodules("keyring.backends"),
        "stock_watcher.providers.tushare.capability_router",
        "stock_watcher.providers.tushare.fast_transport",
        "stock_watcher.providers.tushare.provider",
        "stock_watcher.providers.tushare.super_transport",
        "stock_watcher.providers.tdxquant",
        "stock_watcher.providers.tdxquant_preflight",
        "stock_watcher.providers.tdxquant_m0",
        "stock_watcher.ui.app",
        "stock_watcher.ui.data_source_settings",
        "stock_watcher.ui.tdx_session",
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
