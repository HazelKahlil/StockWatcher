from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORTABLE_SOURCE = ROOT / "packaging" / "windows" / "portable"
APPLICATION_SOURCE = ROOT / "src" / "stock_watcher"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _commit() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()


def build(output: Path) -> tuple[Path, str, int]:
    commit = _commit()
    parent = subprocess.check_output(
        ["git", "rev-parse", "HEAD^"], cwd=ROOT, text=True
    ).strip()
    root_name = "StockWatcher-Internal-Portable"
    with tempfile.TemporaryDirectory(prefix="stockwatcher-portable-") as temporary:
        staging = Path(temporary) / root_name
        (staging / "portable").mkdir(parents=True)
        (staging / "app" / "src").mkdir(parents=True)
        source_map = {
            "启动 StockWatcher.vbs": PORTABLE_SOURCE / "启动 StockWatcher.vbs",
            "portable/stockwatcher_portable.py": PORTABLE_SOURCE
            / "stockwatcher_portable.py",
            "第一次使用.md": PORTABLE_SOURCE / "第一次使用.md",
            "DEPENDENCIES.md": PORTABLE_SOURCE / "DEPENDENCIES.md",
        }
        for relative, source in source_map.items():
            shutil.copyfile(source, staging / relative)
        shutil.copytree(
            APPLICATION_SOURCE,
            staging / "app" / "src" / "stock_watcher",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
        )
        shutil.copyfile(ROOT / "pyproject.toml", staging / "app" / "pyproject.toml")
        shutil.copyfile(ROOT / "uv.lock", staging / "app" / "uv.lock")
        (staging / "SOURCE_COMMIT.txt").write_text(
            f"commit={commit}\nparent={parent}\n",
            encoding="utf-8",
        )
        manifest_targets = tuple(
            path.relative_to(staging).as_posix()
            for path in sorted(staging.rglob("*"))
            if path.is_file()
        )
        manifest = "".join(
            f"{_sha256(staging / relative)}  {relative}\n" for relative in manifest_targets
        )
        (staging / "MANIFEST.sha256").write_text(manifest, encoding="utf-8")
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary_zip = output.with_suffix(".tmp.zip")
        temporary_zip.unlink(missing_ok=True)
        with zipfile.ZipFile(
            temporary_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as archive:
            for path in sorted(staging.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(staging.parent))
        temporary_zip.replace(output)
    return output, _sha256(output), len(manifest_targets) + 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the internal offline portable ZIP")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "dist" / "StockWatcher-Internal-Portable.zip",
    )
    args = parser.parse_args()
    output, digest, count = build(args.output.resolve())
    print(f"{output.name}: sha256={digest}; files={count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
