#!/usr/bin/env bash
# Read-only SQLite preflight for the tunnel DB volume.
# On WAL/SHM I/O or corruption, isolate sidecars into corrupt-<UTC>/ and
# re-check the main file. Never deletes database files. Never auto-restores.
set -Eeuo pipefail

DOCKER=/usr/local/bin/docker
IMAGE=${STOCKWATCHER_PREFLIGHT_IMAGE:-stockwatcher-web:web-alpha4-34ce825}
VOLUME=${STOCKWATCHER_PREFLIGHT_DB_VOLUME:-stockwatcher_stockwatcher_tunnel_db}
DB_NAME=${STOCKWATCHER_PREFLIGHT_DB_NAME:-stockwatcher.db}

if [[ ! -x "$DOCKER" ]]; then
  echo "db-preflight: docker not found at $DOCKER" >&2
  exit 2
fi

"$DOCKER" run --rm --user 10001:10001 \
  -e STOCKWATCHER_PREFLIGHT_DB_NAME="$DB_NAME" \
  -v "${VOLUME}:/var/lib/stockwatcher/db" \
  "$IMAGE" \
  python -s -c '
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

db_dir = Path("/var/lib/stockwatcher/db")
db_name = os.environ.get("STOCKWATCHER_PREFLIGHT_DB_NAME", "stockwatcher.db")
db_path = db_dir / db_name
wal_path = db_dir / f"{db_name}-wal"
shm_path = db_dir / f"{db_name}-shm"


def quick_check(path: Path, readonly: bool) -> tuple[bool, str]:
    if not path.is_file():
        return False, "main database file is missing"
    try:
        if readonly:
            connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        else:
            connection = sqlite3.connect(str(path))
        try:
            row = connection.execute("PRAGMA quick_check").fetchone()
        finally:
            connection.close()
    except sqlite3.Error as error:
        return False, f"{type(error).__name__}: {error}"
    except OSError as error:
        return False, f"{type(error).__name__}: {error}"
    if row is None:
        return False, "quick_check returned no row"
    message = str(row[0])
    return message == "ok", message


def sidecar_ok() -> tuple[bool, str]:
    for path in (wal_path, shm_path):
        if not path.exists():
            continue
        if path.is_symlink() or not path.is_file():
            return False, path.name + " is not a regular file"
        try:
            with path.open("rb") as handle:
                handle.read(4096)
                handle.seek(0, 2)
                size = handle.tell()
                if size > 4096:
                    handle.seek(max(0, size - 4096))
                    handle.read(4096)
        except OSError as error:
            return False, path.name + " " + type(error).__name__ + ": " + str(error)
    return True, "sidecars readable"


def inspect_database(path: Path) -> tuple[bool, str]:
    ok_side, side_message = sidecar_ok()
    if not ok_side:
        return False, side_message
    ok, message = quick_check(path, readonly=True)
    if not ok:
        return False, "readonly " + message
    if wal_path.exists() or shm_path.exists():
        ok_rw, message_rw = quick_check(path, readonly=False)
        if not ok_rw:
            return False, "readwrite " + message_rw
        return True, "readonly=" + message + " readwrite=" + message_rw
    return True, "readonly=" + message


print(f"db-preflight: checking {db_path} on volume")
ok, message = inspect_database(db_path)
if ok:
    print(f"db-preflight: PRAGMA quick_check passed ({message})")
    raise SystemExit(0)

print(f"db-preflight: quick_check failed: {message}", file=sys.stderr)
stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
quarantine = db_dir / f"corrupt-{stamp}"
moved: list[str] = []
for sidecar in (wal_path, shm_path):
    if sidecar.exists() or sidecar.is_symlink():
        quarantine.mkdir(parents=True, exist_ok=True)
        destination = quarantine / sidecar.name
        sidecar.rename(destination)
        moved.append(str(destination))

if moved:
    print("db-preflight: isolated WAL/SHM into " + str(quarantine))
    for item in moved:
        print(f"db-preflight: moved {item}")
else:
    print("db-preflight: no WAL/SHM sidecars present to isolate")

ok, message = inspect_database(db_path)
if ok:
    print(
        "db-preflight: main database passed quick_check after WAL isolation; "
        "SQLite will recreate WAL on the next writer"
    )
    raise SystemExit(0)

print(
    "db-preflight: main database still failing after WAL isolation: " + message,
    file=sys.stderr,
)
print(
    "HUMAN INTERVENTION REQUIRED: leave the live files in place. "
    "Stop web/worker, then restore a verified backup with "
    "python -m stock_watcher.server.admin_cli restore --input "
    "/backups/<verified-backup-dir>. "
    "Do not auto-overwrite the main database.",
    file=sys.stderr,
)
raise SystemExit(1)
'
