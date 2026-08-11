# -*- mode: python ; coding: utf-8 -*-
import os
import subprocess
from pathlib import Path

project_root = Path(SPECPATH).parent
assets_dir = project_root / "src" / "stock_watcher" / "ui" / "assets"
macos_icon = assets_dir / "stockwatcher-macos.png"


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
    (str(assets_dir / "stockwatcher-macos.png"), "stock_watcher/ui/assets"),
    (str(assets_dir / "stockwatcher.png"), "stock_watcher/ui/assets"),
    (str(source_commit_file), "stock_watcher"),
]
seed = os.environ.get("STOCKWATCHER_UNIVERSE_SEED_PATH", "").strip()
if seed and Path(seed).is_file():
    datas.append((str(Path(seed)), "stock_watcher/data"))

analysis = Analysis(
    [str(project_root / "src" / "stock_watcher" / "__main__.py")],
    pathex=[str(project_root / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "objc",
        "Foundation",
        "PyObjCTools",
        "tushare",
        "tushare.stock",
        "tushare.stock.cons",
        "tushare.stock.rtq",
        "stock_watcher.providers.tushare.capability_router",
        "stock_watcher.providers.tushare.native_realtime_transport",
        "stock_watcher.providers.tushare.pro_proxy_transport",
        "stock_watcher.providers.tushare.provider",
        "stock_watcher.providers.tushare.super_transport",
        "stock_watcher.providers.tushare.unified_provider",
        "stock_watcher.runtime.data_health",
        "stock_watcher.runtime.automation",
        "stock_watcher.runtime.market_session",
        "stock_watcher.runtime.outcome_tracker",
        "stock_watcher.runtime.post_close_pdf",
        "stock_watcher.runtime.post_close_review",
        "stock_watcher.runtime.scan_coordinator",
        "stock_watcher.runtime.tushare_runtime",
        "stock_watcher.ui.app",
        "stock_watcher.ui.daily_summary",
        "stock_watcher.ui.data_source_settings",
        "stock_watcher.ui.outcome_review",
        "stock_watcher.ui.tushare_v1_session",
        "stock_watcher.ui.tushare_session",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "tqcenter",
        "keyring.backends.SecretService",
        "keyring.backends.Windows",
        "keyring.backends.kwallet",
        "keyring.backends.libsecret",
        "stock_watcher.providers.tdxquant",
        "stock_watcher.providers.tdxquant_m0",
        "stock_watcher.providers.tdxquant_preflight",
        "stock_watcher.ui.tdx_session",
    ],
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
    upx=False,
    console=False,
)
collection = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="StockWatcher",
)
app = BUNDLE(
    collection,
    name="StockWatcher.app",
    icon=str(macos_icon),
    bundle_identifier="com.kahlilhazel.stockwatcher",
    version="0.6.0a3",
    info_plist={
        "CFBundleDevelopmentRegion": "zh_CN",
        "CFBundleDisplayName": "StockWatcher",
        "CFBundleName": "StockWatcher",
        "LSMinimumSystemVersion": "13.0",
        "NSHighResolutionCapable": True,
        "NSSupportsAutomaticGraphicsSwitching": True,
    },
)
