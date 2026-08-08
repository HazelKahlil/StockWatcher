"""Admin CLI: migrate, create-user, backup, restore, provider-preflight.

Usage:
    python -m stock_watcher.server.admin_cli migrate
    python -m stock_watcher.server.admin_cli create-user --username NAME --role admin \\
        [--password-stdin]
    python -m stock_watcher.server.admin_cli backup [--output DIR]
    python -m stock_watcher.server.admin_cli restore --input DIR
    python -m stock_watcher.server.admin_cli provider-preflight --scales 1,100,300,800,full
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from stock_watcher.build_info import source_commit
from stock_watcher.domain import SHANGHAI
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


def _read_password(args: argparse.Namespace) -> str:
    if args.password_stdin:
        return str(sys.stdin.readline().rstrip("\n"))
    if args.password is not None:
        return str(args.password)
    value = getpass.getpass("Password (min 12 chars, stdin): ")
    return str(value)


def cmd_create_user(settings: ServerSettings, args: argparse.Namespace) -> int:
    from .auth import AuthService

    store = _store(settings)
    auth = AuthService(store)
    password = _read_password(args)
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


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def cmd_backup(settings: ServerSettings, args: argparse.Namespace) -> int:
    """SQLite backup API snapshot + reports + manifest with hashes.

    The database is copied with the backup API (never by copying a live
    .db/.wal/.shm), reports are copied as files, and a SHA-256 manifest is
    written for restore verification.
    """
    store = _store(settings)
    output = Path(args.output) if args.output else settings.report_dir.parent / "backups"
    output.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%SZ")
    target = output / f"stockwatcher-{stamp}"
    target.mkdir(parents=True, exist_ok=True)
    db_backup = target / "stockwatcher.sqlite3"
    store.backup(db_backup)
    reports_target = target / "reports"
    if settings.report_dir.is_dir():
        shutil.copytree(settings.report_dir, reports_target, dirs_exist_ok=True)
    with store.connect() as connection:
        version = connection.execute(
            "SELECT version FROM schema_version ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
    files: dict[str, str] = {}
    for path in sorted(target.rglob("*")):
        if path.is_file() and path.name != "SHA256SUMS.txt":
            files[str(path.relative_to(target))] = _sha256_file(path)
    manifest = {
        "backup_dir": str(target),
        "created_at": datetime.now().isoformat(),
        "schema_version": int(version[0]) if version else None,
        "source_commit": (
            settings.source_commit
            if settings.source_commit != "unknown"
            else source_commit()
        ),
        "file_count": len(files),
        "files": files,
    }
    (target / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    with (target / "SHA256SUMS.txt").open("w", encoding="utf-8") as handle:
        for name, digest in sorted(files.items()):
            handle.write(f"{digest}  {name}\n")
    print(json.dumps({**manifest, "files": None}, ensure_ascii=False, indent=2))
    return 0


def cmd_restore(settings: ServerSettings, args: argparse.Namespace) -> int:
    backup_dir = Path(args.input)
    if not backup_dir.is_dir():
        print(f"backup directory not found: {backup_dir}", file=sys.stderr)
        return 1
    checksum = backup_dir / "SHA256SUMS.txt"
    if checksum.is_file():
        result = __import__("subprocess").run(
            ["sha256sum", "-c", "SHA256SUMS.txt"],
            cwd=str(backup_dir),
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"backup checksum verification failed:\n{result.stdout}", file=sys.stderr)
            return 1
    db_backup = backup_dir / "stockwatcher.sqlite3"
    if not db_backup.is_file():
        print(f"backup database missing: {db_backup}", file=sys.stderr)
        return 1
    # Do not call _store() here: initialize() intentionally fails closed on a
    # malformed current database, while restore is the recovery path that must
    # still be able to replace that database from a verified backup.
    store = SQLiteStore(settings.db_path)
    store.rollback(db_backup)
    reports_backup = backup_dir / "reports"
    if reports_backup.is_dir():
        settings.report_dir.mkdir(parents=True, exist_ok=True)
        shutil.copytree(reports_backup, settings.report_dir, dirs_exist_ok=True)
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
                "restored": str(backup_dir),
                "integrity_check": integrity[0],
                "schema_version": int(version[0]) if version else None,
                "reports_restored": reports_backup.is_dir(),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def cmd_provider_preflight(settings: ServerSettings, args: argparse.Namespace) -> int:
    """Layered data-source probe using the active encrypted token.

    Runs only inside the Worker container (master key + active token are
    available there). Measures elapsed/coverage/age per scale and writes a
    machine-readable JSON report; never prints the token.
    """
    from stock_watcher.config import DataSourceSettings
    from stock_watcher.providers.tushare import TushareSdkProTransport
    from stock_watcher.providers.tushare.native_realtime_transport import (
        NativeRealtimeTransport,
    )
    from stock_watcher.providers.tushare.rate_limit import ApplicationRequestBudget
    from stock_watcher.providers.tushare.transport_protocol import TransportRequest
    from stock_watcher.services.secret_service import SecretService, load_master_key

    store = _store(settings)
    secrets = SecretService(
        store,
        master_key=load_master_key(settings.require_master_key()),
        environment=settings.environment,
        key_version=settings.secret_key_version,
    )
    token = secrets.active_token()
    if not token:
        print("no active token; preflight cannot run", file=sys.stderr)
        return 1
    ds = DataSourceSettings()
    budget = ApplicationRequestBudget(ds.request_budget_interval_seconds)
    report: dict[str, Any] = {
        "run_at": datetime.now(SHANGHAI).isoformat(),
        "business_timezone": "Asia/Shanghai",
        "source_commit": settings.source_commit,
        "active_token_fingerprint": secrets.active_fingerprint(),
        "layers": [],
        "ok": False,
    }
    try:
        pro = TushareSdkProTransport(
            ds.primary_profile,
            lambda: token,
            request_budget=budget,
        )
        started = time.monotonic()
        stocks = pro.execute(
            TransportRequest(
                endpoint="/",
                api_name="stock_basic",
                params={"exchange": "", "list_status": "L"},
                fields=("ts_code", "name", "industry"),
            )
        )
        report["layers"].append(
            {
                "layer": "static_pro",
                "endpoint": str(ds.primary_profile.base_url),
                "ok": stocks.records is not None,
                "rows": len(stocks.records or []),
                "elapsed_seconds": round(time.monotonic() - started, 3),
            }
        )
        if stocks.records is None:
            report["error"] = "static_pro unavailable"
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 1
        stock_rows: list[Any] = [
            row for row in (stocks.records or []) if isinstance(row, dict)
        ]
        codes = [
            str(row.get("ts_code", ""))
            for row in stock_rows
            if isinstance(row, dict) and row.get("ts_code")
        ]
        realtime = NativeRealtimeTransport(
            ds.native_realtime_profile,
            lambda: token,
            request_budget=budget,
        )
        scales = [int(value) for value in str(args.scales).split(",") if value.strip()]
        if "full" in str(args.scales).split(","):
            scales.append(len(codes))
        for size in dict.fromkeys(scales):
            batch = codes[:size]
            started = time.monotonic()
            try:
                response = realtime.execute(
                    TransportRequest(
                        endpoint="tushare.realtime_quote:sina",
                        api_name="realtime_quote",
                        params={"ts_code": ",".join(batch)},
                        fields=(
                            "ts_code",
                            "name",
                            "open",
                            "pre_close",
                            "price",
                            "high",
                            "low",
                            "vol",
                            "amount",
                            "source_ts",
                            "received_ts",
                        ),
                        realtime=True,
                        method="SDK",
                    )
                )
                rows: list[Any] = [
                    row for row in (response.records or []) if isinstance(row, dict)
                ]
                source_age = None
                source_span = None
                timestamps: list[datetime] = []
                for row in rows:
                    stamp_value = row.get("received_ts") or row.get("source_ts")
                    if not stamp_value:
                        continue
                    try:
                        timestamps.append(datetime.fromisoformat(str(stamp_value)))
                    except ValueError:
                        continue
                if timestamps:
                    source_age = max(
                        (datetime.now(SHANGHAI) - ts).total_seconds()
                        for ts in timestamps
                    )
                    source_span = (max(timestamps) - min(timestamps)).total_seconds()
                report["layers"].append(
                    {
                        "layer": f"realtime_{size}",
                        "endpoint": "tushare.realtime_quote(src=sina)",
                        "verify_url": str(ds.native_realtime_profile.verify_url),
                        "ok": len(rows) > 0,
                        "requested": size,
                        "rows": len(rows),
                        "elapsed_seconds": round(time.monotonic() - started, 3),
                        "max_source_age_seconds": (
                            round(source_age, 2) if source_age is not None else None
                        ),
                        "source_span_seconds": (
                            round(source_span, 2) if source_span is not None else None
                        ),
                    }
                )
                if not rows:
                    report["error"] = f"realtime_{size} empty"
                    break
            except Exception as error:
                report["layers"].append(
                    {
                        "layer": f"realtime_{size}",
                        "ok": False,
                        "error": type(error).__name__,
                        "elapsed_seconds": round(time.monotonic() - started, 3),
                    }
                )
                report["error"] = f"realtime_{size} failed: {type(error).__name__}"
                break
        report["ok"] = all(
            bool(layer.get("ok"))
            for layer in report["layers"]
            if isinstance(layer, dict)
        )
    except Exception as error:
        report["error"] = type(error).__name__
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.json_output:
        output = Path(args.json_output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["ok"] else 1


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
        help="read from --password-stdin or interactive prompt; never pass on argv",
    )
    create.add_argument(
        "--password-stdin",
        action="store_true",
        help="read the password from stdin (one line)",
    )
    backup = sub.add_parser("backup", help="SQLite backup API snapshot + reports + manifest")
    backup.add_argument("--output", default=None)
    restore = sub.add_parser("restore", help="restore from a backup directory")
    restore.add_argument("--input", required=True)
    preflight = sub.add_parser(
        "provider-preflight",
        help="layered data-source probe with the active token (worker only)",
    )
    preflight.add_argument(
        "--scales",
        default="1,100,300,800,full",
        help="comma-separated scales, 'full' = whole universe",
    )
    preflight.add_argument("--json-output", default=None)
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
    if args.command == "provider-preflight":
        return cmd_provider_preflight(settings, args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
