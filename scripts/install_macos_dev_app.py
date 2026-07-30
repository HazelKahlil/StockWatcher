from __future__ import annotations

import argparse
import plistlib
import shutil
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSET_ICON = ROOT / "src" / "stock_watcher" / "ui" / "assets" / "stockwatcher-macos.png"
APP_NAME = "StockWatcher Dev.app"
EXECUTABLE_NAME = "StockWatcherDev"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Install a local macOS development launcher for StockWatcher."
    )
    parser.add_argument(
        "--install-dir",
        type=Path,
        default=Path.home() / "Applications",
        help="Directory that will receive StockWatcher Dev.app.",
    )
    parser.add_argument(
        "--no-sign",
        action="store_true",
        help="Skip ad-hoc codesign after creating the app wrapper.",
    )
    args = parser.parse_args()

    if not ASSET_ICON.is_file():
        raise SystemExit(f"missing macOS icon: {ASSET_ICON}")

    install_dir = args.install_dir.expanduser().resolve()
    app_path = install_dir / APP_NAME
    if app_path.name != APP_NAME:
        raise SystemExit("refusing to install to an unexpected app name")

    install_dir.mkdir(parents=True, exist_ok=True)
    if app_path.exists():
        shutil.rmtree(app_path)

    contents = app_path / "Contents"
    macos = contents / "MacOS"
    resources = contents / "Resources"
    macos.mkdir(parents=True)
    resources.mkdir()

    _write_plist(contents / "Info.plist")
    _write_launcher(macos / EXECUTABLE_NAME)
    _write_icon(resources)

    if not args.no_sign and shutil.which("codesign"):
        subprocess.run(
            ["codesign", "--force", "--deep", "--sign", "-", str(app_path)],
            check=True,
        )

    print(app_path)
    return 0


def _write_plist(path: Path) -> None:
    payload = {
        "CFBundleDevelopmentRegion": "zh_CN",
        "CFBundleDisplayName": "StockWatcher Dev",
        "CFBundleExecutable": EXECUTABLE_NAME,
        "CFBundleIconFile": "stockwatcher-macos",
        "CFBundleIdentifier": "com.kahlilhazel.stockwatcher.dev",
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleName": "StockWatcher Dev",
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": "0.4.0-dev",
        "CFBundleVersion": "0.4.0-dev",
        "LSMinimumSystemVersion": "13.0",
        "NSHighResolutionCapable": True,
    }
    with path.open("wb") as handle:
        plistlib.dump(payload, handle, sort_keys=True)


def _write_launcher(path: Path) -> None:
    path_value = (
        "/Users/kahlilhazel/.local/bin:/opt/homebrew/bin:/usr/local/bin:"
        "/usr/bin:/bin:/usr/sbin:/sbin"
    )
    app_command = (
        'exec uv run python -m stock_watcher.ui.app --provider tushare '
        '>> "$log_dir/stockwatcher-dev-app.log" 2>&1'
    )
    log_dir = "$HOME/Library/Logs/StockWatcher"
    launcher = f"""#!/bin/zsh
set -e
export PATH={_zsh_quote(path_value)}
project_root={_zsh_quote(str(ROOT))}
log_dir="{log_dir}"
mkdir -p "$log_dir"
cd "$project_root"
{app_command}
"""
    path.write_text(launcher, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _write_icon(resources: Path) -> None:
    iconset = resources / "stockwatcher-macos.iconset"
    iconset.mkdir()
    sizes = [
        (16, "icon_16x16.png"),
        (32, "icon_16x16@2x.png"),
        (32, "icon_32x32.png"),
        (64, "icon_32x32@2x.png"),
        (128, "icon_128x128.png"),
        (256, "icon_128x128@2x.png"),
        (256, "icon_256x256.png"),
        (512, "icon_256x256@2x.png"),
        (512, "icon_512x512.png"),
        (1024, "icon_512x512@2x.png"),
    ]
    for pixels, filename in sizes:
        subprocess.run(
            [
                "sips",
                "-z",
                str(pixels),
                str(pixels),
                str(ASSET_ICON),
                "--out",
                str(iconset / filename),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )
    subprocess.run(
        [
            "iconutil",
            "-c",
            "icns",
            str(iconset),
            "-o",
            str(resources / "stockwatcher-macos.icns"),
        ],
        check=True,
    )
    shutil.rmtree(iconset)


def _zsh_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


if __name__ == "__main__":
    raise SystemExit(main())
