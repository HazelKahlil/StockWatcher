from __future__ import annotations

import argparse
import shutil
from datetime import datetime
from pathlib import Path

from stock_watcher.domain import SHANGHAI
from stock_watcher.runtime import RuntimeUniverseCache


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate and export a credential-free runtime universe seed."
    )
    parser.add_argument("source", type=Path, help="Existing runtime-universe-v1.json")
    parser.add_argument("destination", type=Path, help="Seed output path")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    now = datetime.now(SHANGHAI)
    RuntimeUniverseCache(args.source).load(now=now, allow_stale=True)
    args.destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.destination.with_suffix(args.destination.suffix + ".tmp")
    shutil.copyfile(args.source, temporary)
    temporary.replace(args.destination)
    RuntimeUniverseCache(args.destination).load(now=now, allow_stale=True)
    print(args.destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
