from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from stock_watcher.domain import SHANGHAI
from stock_watcher.runtime.universe_cache import RuntimeUniverseCache
from stock_watcher.runtime.universe_seed import (
    MANIFEST_FILENAME,
    SEED_FILENAME,
    assert_seed_matches_manifest,
    validate_universe_seed,
    write_seed_manifest,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate and export a credential-free runtime universe seed."
    )
    parser.add_argument("source", type=Path, help="Existing runtime-universe-v1.json")
    parser.add_argument("destination", type=Path, help="Seed output path")
    parser.add_argument(
        "--source-commit",
        default=os.environ.get("STOCKWATCHER_SOURCE_COMMIT", "").strip(),
        help="Source commit recorded in the seed manifest",
    )
    parser.add_argument(
        "--first-run-pack",
        action="store_true",
        help="Mark this seed as a first-run installer input",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    now = datetime.now(SHANGHAI)
    RuntimeUniverseCache(args.source).load(now=now, allow_stale=True)
    args.destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.destination.with_suffix(args.destination.suffix + ".tmp")
    shutil.copyfile(args.source, temporary)
    temporary.replace(args.destination)
    summary = validate_universe_seed(args.destination, now=now, allow_stale=True)
    if args.destination.name != SEED_FILENAME:
        raise SystemExit(f"seed destination must be named {SEED_FILENAME}")
    source_commit = args.source_commit
    if not source_commit:
        try:
            source_commit = subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                text=True,
            ).strip()
        except Exception:
            source_commit = "unknown"
    manifest = write_seed_manifest(
        args.destination.with_name(MANIFEST_FILENAME),
        source_commit=source_commit,
        seed_summary=summary,
        first_run_pack=args.first_run_pack,
    )
    assert_seed_matches_manifest(args.destination, manifest)
    print(args.destination)
    print(manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
