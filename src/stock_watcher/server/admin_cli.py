"""Admin CLI: migrate, users, backup, restore and provider preflight.

Usage:
    python -m stock_watcher.server.admin_cli migrate
    python -m stock_watcher.server.admin_cli create-user --username NAME --role admin \\
        [--password-stdin]
    python -m stock_watcher.server.admin_cli reset-password --username NAME \\
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
from datetime import time as wall_time
from pathlib import Path
from typing import Any

from stock_watcher.build_info import source_commit
from stock_watcher.domain import SHANGHAI
from stock_watcher.storage import SQLiteStore

from .config import ServerSettings
from .redaction import redact


def _store(settings: ServerSettings) -> SQLiteStore:
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    store = SQLiteStore(
        settings.db_path,
        recovery_backup_dirs=(settings.backup_dir,),
    )
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


def cmd_reset_password(settings: ServerSettings, args: argparse.Namespace) -> int:
    """Reset one existing user's password without exposing it on argv or stdout."""
    from .auth import AuthError, AuthService

    store = _store(settings)
    auth = AuthService(store)
    username = str(args.username).strip().casefold()
    user = auth.users.get_by_username(username)
    if user is None:
        print("reset-password failed: username does not exist", file=sys.stderr)
        return 1

    password = _read_password(args)
    try:
        password_hash = auth.hash_password(password)
    except AuthError as error:
        print(f"reset-password failed: {redact(str(error))}", file=sys.stderr)
        return 1

    user_id = int(user["user_id"])
    updated = auth.users.update(user_id, password_hash=password_hash)
    if updated is None:
        print("reset-password failed: username does not exist", file=sys.stderr)
        return 1
    revoked_sessions = auth.revoke_user_sessions(user_id)
    auth.audit.record(
        actor_user_id=None,
        action="user.password_reset_cli",
        object_type="user",
        object_id=str(user_id),
        outcome="succeeded",
        detail={"username": username, "revoked_sessions": revoked_sessions},
    )
    print(
        json.dumps(
            {
                "user_id": user_id,
                "username": updated["username"],
                "role": updated["role"],
                "note": "密码与哈希未回显；已有会话已撤销。",
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
    _replace_report_directory(reports_backup, settings.report_dir)
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


def _remove_report_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def _replace_mounted_report_directory(source: Path, target: Path) -> None:
    """Replace a report volume's contents without renaming its mount point."""
    staging = target / ".restore-tmp"
    previous = target / ".restore-old"
    _remove_report_path(staging)
    _remove_report_path(previous)
    if source.is_dir():
        shutil.copytree(source, staging)
    else:
        staging.mkdir()
    previous.mkdir()

    preserved = {staging, previous}
    for path in list(target.iterdir()):
        if path not in preserved:
            path.replace(previous / path.name)
    try:
        for path in list(staging.iterdir()):
            path.replace(target / path.name)
    except BaseException:
        for path in list(target.iterdir()):
            if path not in preserved:
                _remove_report_path(path)
        for path in list(previous.iterdir()):
            path.replace(target / path.name)
        raise
    finally:
        _remove_report_path(staging)
    _remove_report_path(previous)


def _replace_report_directory(source: Path, target: Path) -> None:
    """Replace reports so stale PDFs cannot survive a controlled restore.

    A Docker named volume makes ``target`` a mount point, which cannot be
    renamed even while its contents remain writable. Web and Worker are
    stopped during restore, so stage and roll back entries inside that volume.
    Normal host directories retain the atomic sibling-directory swap.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.is_mount():
        _replace_mounted_report_directory(source, target)
        return
    staging = target.with_name(f"{target.name}.restore-tmp")
    previous = target.with_name(f"{target.name}.restore-old")
    shutil.rmtree(staging, ignore_errors=True)
    shutil.rmtree(previous, ignore_errors=True)
    if source.is_dir():
        shutil.copytree(source, staging)
    else:
        staging.mkdir()
    if target.exists():
        target.replace(previous)
    try:
        staging.replace(target)
    except BaseException:
        if previous.exists() and not target.exists():
            previous.replace(target)
        raise
    shutil.rmtree(previous, ignore_errors=True)


def _parse_preflight_scales(value: str, *, full_count: int) -> list[int]:
    tokens = [part.strip().casefold() for part in value.split(",")]
    scales = [int(part) for part in tokens if part and part != "full"]
    if any(scale < 1 for scale in scales):
        raise ValueError("preflight scales must be positive")
    if "full" in tokens:
        scales.append(full_count)
    return list(dict.fromkeys(scales))


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
            str(row.get("ts_code", "")).strip().upper()
            for row in stock_rows
            if isinstance(row, dict) and row.get("ts_code")
        ]
        unique_codes = set(codes)
        industry_count = sum(1 for row in stock_rows if str(row.get("industry") or "").strip())
        static_ok = (
            len(codes) >= 4_500
            and len(unique_codes) == len(codes)
            and all(code.rpartition(".")[2] in {"SH", "SZ", "BJ"} for code in codes)
            and industry_count / len(codes) >= 0.95
        )
        report["layers"][-1].update(
            {
                "ok": static_ok,
                "unique_codes": len(unique_codes),
                "industry_coverage_ratio": (
                    round(industry_count / len(codes), 6) if codes else 0.0
                ),
            }
        )
        if not static_ok:
            report["error"] = "static_pro coverage or uniqueness invalid"
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 1
        realtime = NativeRealtimeTransport(
            ds.native_realtime_profile,
            lambda: token,
            request_budget=budget,
        )
        scales = _parse_preflight_scales(str(args.scales), full_count=len(codes))
        for size in scales:
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
                source_age: float | None = None
                source_span: float | None = None
                timestamps: list[datetime] = []
                for row in rows:
                    stamp_value = row.get("source_ts")
                    if not stamp_value:
                        continue
                    try:
                        stamp = datetime.fromisoformat(str(stamp_value))
                    except ValueError:
                        continue
                    if stamp.tzinfo is None:
                        continue
                    timestamps.append(stamp.astimezone(SHANGHAI))
                observed_codes = [
                    str(row.get("ts_code") or "").strip().upper() for row in rows
                ]
                expected_codes = set(batch)
                observed_unique = set(observed_codes)
                now = datetime.now(SHANGHAI)
                if timestamps:
                    source_age = max(
                        (now - ts).total_seconds()
                        for ts in timestamps
                    )
                    source_span = (max(timestamps) - min(timestamps)).total_seconds()
                source_times_valid = bool(timestamps) and len(timestamps) == len(rows) and all(
                    ts.date() == now.date()
                    and (
                        wall_time(9, 30) <= ts.timetz().replace(tzinfo=None) <= wall_time(11, 30)
                        or wall_time(13, 0)
                        <= ts.timetz().replace(tzinfo=None)
                        <= wall_time(15, 0)
                    )
                    for ts in timestamps
                )
                coverage = (
                    len(observed_unique & expected_codes) / len(expected_codes)
                    if expected_codes
                    else 0.0
                )
                realtime_ok = (
                    len(rows) == len(observed_codes)
                    and len(observed_unique) == len(observed_codes)
                    and observed_unique <= expected_codes
                    and coverage >= 0.99
                    and source_times_valid
                    and source_age is not None
                    and -10.0 <= source_age <= ds.source_fresh_seconds
                    and source_span is not None
                    and source_span <= ds.full_scan_max_seconds
                )
                report["layers"].append(
                    {
                        "layer": f"realtime_{size}",
                        "endpoint": "tushare.realtime_quote(src=sina)",
                        "verify_url": str(ds.native_realtime_profile.verify_url),
                        "ok": realtime_ok,
                        "requested": size,
                        "rows": len(rows),
                        "unique_codes": len(observed_unique),
                        "coverage_ratio": round(coverage, 6),
                        "source_times_valid": source_times_valid,
                        "elapsed_seconds": round(time.monotonic() - started, 3),
                        "max_source_age_seconds": (
                            round(source_age, 2) if source_age is not None else None
                        ),
                        "source_span_seconds": (
                            round(source_span, 2) if source_span is not None else None
                        ),
                    }
                )
                if not realtime_ok:
                    report["error"] = f"realtime_{size} validation failed"
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
        "--password-stdin",
        action="store_true",
        help="read the password from stdin (one line)",
    )
    reset = sub.add_parser(
        "reset-password",
        help="reset an existing user's password and revoke its sessions",
    )
    reset.add_argument("--username", required=True)
    reset.add_argument(
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
    if args.command == "reset-password":
        return cmd_reset_password(settings, args)
    if args.command == "backup":
        return cmd_backup(settings, args)
    if args.command == "restore":
        return cmd_restore(settings, args)
    if args.command == "provider-preflight":
        return cmd_provider_preflight(settings, args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
