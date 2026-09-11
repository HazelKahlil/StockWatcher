from __future__ import annotations

import argparse
from pathlib import Path

from stock_watcher.runtime.universe_seed import assert_seed_matches_manifest


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify a bundled universe seed against its manifest."
    )
    parser.add_argument("seed", type=Path)
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    assert_seed_matches_manifest(args.seed, args.manifest)
    print(args.seed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
