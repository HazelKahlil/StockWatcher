"""Build paired Mac/Web candidates from one clean, immutable Git archive.

Does not install an App, run containers, modify runtime data or publish images.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import runpy
import subprocess
import sys
import tarfile
from pathlib import Path


def run(*args: str, cwd: Path, env: dict[str, str] | None = None) -> None:
    subprocess.run(args, cwd=cwd, env=env, check=True)


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mac", action="store_true")
    parser.add_argument("--web", action="store_true")
    args = parser.parse_args()
    if not args.mac and not args.web:
        parser.error("choose --mac and/or --web")
    root = Path(__file__).resolve().parents[1]
    dirty = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=normal"],
        cwd=root,
        text=True,
    )
    if dirty.strip():
        raise SystemExit("Commit the candidate before building; working tree is not clean.")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    output = args.output.resolve() / commit[:12]
    output.mkdir(parents=True, exist_ok=False)
    archive = output / "source.tar"
    run("git", "archive", "--format=tar", f"--output={archive}", commit, cwd=root)
    source = output / "source"
    source.mkdir()
    with tarfile.open(archive) as stream:
        stream.extractall(source, filter="data")
    version = runpy.run_path(str(source / "src/stock_watcher/__init__.py"))["__version__"]
    display = version.replace("a", "-alpha.")
    manifest: dict[str, object] = {
        "version": display,
        "python_version": version,
        "source_commit": commit,
        "source_archive_sha256": digest(archive),
        "source_was_clean": True,
        "installed": False,
        "deployed": False,
        "windows_built": False,
    }
    env = os.environ.copy()
    env.pop("STOCKWATCHER_UNIVERSE_SEED_PATH", None)
    env["STOCKWATCHER_SOURCE_COMMIT"] = commit
    env["STOCKWATCHER_BUILD_VERSION"] = display
    try:
        if args.mac:
            if sys.platform != "darwin":
                raise SystemExit("The Mac candidate must be built on macOS.")
            run(
                sys.executable,
                "-m",
                "PyInstaller",
                "--noconfirm",
                "--clean",
                "--distpath",
                str(output / "mac"),
                "--workpath",
                str(output / "mac-build"),
                str(source / "packaging/stockwatcher-macos.spec"),
                cwd=source,
                env=env,
            )
            app = output / "mac/StockWatcher.app"
            run("codesign", "--force", "--deep", "--sign", "-", str(app), cwd=source)
            run("codesign", "--verify", "--deep", "--strict", str(app), cwd=source)
            info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
            embedded = (app / "Contents/Resources/stock_watcher/SOURCE_COMMIT").read_text().strip()
            if info["CFBundleShortVersionString"] != display or embedded != commit:
                raise SystemExit("Mac version/source identity mismatch.")
            zipped = output / f"StockWatcher-{display}-macOS-arm64.zip"
            run(
                "ditto",
                "-c",
                "-k",
                "--sequesterRsrc",
                "--keepParent",
                str(app),
                str(zipped),
                cwd=source,
            )
            manifest["mac"] = {
                "app": str(app),
                "zip": str(zipped),
                "zip_sha256": digest(zipped),
                "executable_sha256": digest(app / "Contents/MacOS/StockWatcher"),
                "signature": "ad-hoc",
                "source_commit": embedded,
            }
        if args.web:
            tag = f"stockwatcher-web:mac-web-{display}-{commit[:7]}"
            run(
                "docker",
                "build",
                "-f",
                "deploy/Dockerfile",
                "-t",
                tag,
                "--build-arg",
                f"SOURCE_COMMIT={commit}",
                "--build-arg",
                f"BUILD_VERSION={display}",
                ".",
                cwd=source,
            )
            image_id = subprocess.check_output(
                ["docker", "image", "inspect", "--format", "{{.Id}}", tag],
                text=True,
            ).strip()
            manifest["web"] = {"image": tag, "image_id": image_id, "source_commit": commit}
    finally:
        (output / "release.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
