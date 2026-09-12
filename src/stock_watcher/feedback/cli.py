"""Explicit offline migration entry; never run automatically during app startup."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .schema import migrate_with_backup


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Install candidate approval extension on an offline DB"
    )
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--backup-dir", type=Path, required=True)
    parser.add_argument("--confirm-offline", action="store_true")
    args = parser.parse_args()
    if not args.confirm_offline:
        parser.error("stop writers, obtain owner approval, then explicitly use --confirm-offline")
    try:
        result = migrate_with_backup(args.db, args.backup_dir)
    except Exception as error:
        # Never echo database contents, SQL parameters or credentials.
        print(f"migration failed ({type(error).__name__}); do not enable feedback", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
