"""Admin CLI: migrate, create-user, backup, restore.

Usage:
    python -m stock_watcher.server.admin_cli migrate
    python -m stock_watcher.server.admin_cli create-user --username NAME --role admin
    python -m stock_watcher.server.admin_cli backup [--output PATH]
    python -m stock_watcher.server.admin_cli restore --backup PATH
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

from stock_watcher.build_info import source_commit
from stock_watcher.storage import SQLiteStore

from .config import ServerSettings
from .redaction import redact


def _store(settings: ServerSettings) -> SQLiteStore:
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    store = SQLiteStore(settings.db_path)
    store.initialize()
    return store


def cmd_migrate(settings: ServerSettings, args: argparse.Namespace) -> int:
    store = _store(settings)
    with store.connect() as connection:
        version = connection.execute(
            "SELECT version FROM schema_version ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
    print(
        json.dumps(
            {
                "schema_version": int(version[0]) if version else None,
                "integrity_check": integrity[0] if integrity else None,
                "foreign_key_issues": len(foreign_keys),
                "database": str(settings.db_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if integrity != ("ok",) or foreign_keys:
        print("migration validation FAILED", file=sys.stderr)
        return 1
    print("migration validation passed")
    return 0


def cmd_create_user(settings: ServerSettings, args: argparse.Namespace) -> int:
    from .auth import AuthService

    store = _store(settings)
    auth = AuthService(store)
    password = args.password
    if password is None:
        password = getpass.getpass("Password (min 12 chars, stdin): ")
    try:
        user = auth.create_user(
            username=args.username,
            password=password,
            role=args.role,
            created_by=None,
        )
    except Exception as error:
        print(f"create-user failed: {redact(str(error))}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "user_id": user["user_id"],
                "username": user["username"],
                "role": user["role"],
                "note": "密码与哈希未回显；登录走 HTTPS 页面。",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def cmd_backup(settings: ServerSettings, args: argparse.Namespace) -> int:
    store = _store(settings)
    output = args.output or settings.report_dir.parent / "backups"
    output.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    destination = output / f"stockwatcher-{stamp}.sqlite3"
    store.backup(destination)
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    with store.connect() as connection:
        version = connection.execute(
            "SELECT version FROM schema_version ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
    metadata = {
        "backup": str(destination),
        "sha256": digest,
        "schema_version": int(version[0]) if version else None,
        "source_commit": (
            settings.source_commit
            if settings.source_commit != "unknown"
            else source_commit()
        ),
        "created_at": datetime.now().isoformat(),
        "files": 1,
    }
    (destination.with_suffix(".json")).write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    return 0


def cmd_restore(settings: ServerSettings, args: argparse.Namespace) -> int:
    backup = Path(args.backup)
    if not backup.is_file():
        print(f"backup not found: {backup}", file=sys.stderr)
        return 1
    store = _store(settings)
    store.rollback(backup)
    with store.connect() as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        version = connection.execute(
            "SELECT version FROM schema_version ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
    if integrity != ("ok",):
        print("restore failed integrity check", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "restored": str(backup),
                "integrity_check": integrity[0],
                "schema_version": int(version[0]) if version else None,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def parse_args() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="stock_watcher.admin_cli")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate", help="run idempotent schema migration and validate")
    create = sub.add_parser("create-user", help="create the first Admin or a Tester")
    create.add_argument("--username", required=True)
    create.add_argument("--role", choices=["tester", "admin"], default="tester")
    create.add_argument(
        "--password",
        default=None,
        help="read from stdin when omitted; never pass on the command line",
    )
    backup = sub.add_parser("backup", help="SQLite backup API snapshot")
    backup.add_argument("--output", default=None)
    restore = sub.add_parser("restore", help="restore from a backup file")
    restore.add_argument("--backup", required=True)
    return parser


def main() -> int:
    args = parse_args().parse_args()
    settings = ServerSettings.from_env()
    if args.command == "migrate":
        return cmd_migrate(settings, args)
    if args.command == "create-user":
        return cmd_create_user(settings, args)
    if args.command == "backup":
        return cmd_backup(settings, args)
    if args.command == "restore":
        return cmd_restore(settings, args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
