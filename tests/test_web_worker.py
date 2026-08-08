"""WRK-001: only one Worker owns the lease; a second instance exits safely.

Runs the real ``stock_watcher.server.worker`` entry point in subprocesses
against one database and one master key.
"""
from __future__ import annotations

import base64
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path


def _prepare(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    from stock_watcher.storage import SQLiteStore

    db = tmp_path / "state" / "db" / "stockwatcher.db"
    db.parent.mkdir(parents=True)
    SQLiteStore(db).initialize()
    key_file = tmp_path / "master.key"
    key_file.write_text(base64.urlsafe_b64encode(os.urandom(32)).decode("ascii"))
    env = {
        **os.environ,
        "STOCKWATCHER_ENV": "test",
        "STOCKWATCHER_DB_PATH": str(db),
        "STOCKWATCHER_REPORT_DIR": str(tmp_path / "reports"),
        "STOCKWATCHER_MASTER_KEY_FILE": str(key_file),
        "STOCKWATCHER_BUSINESS_TIMEZONE": "Asia/Shanghai",
        "STOCKWATCHER_PUBLIC_ORIGIN": "http://127.0.0.1:8000",
        "STOCKWATCHER_SOURCE_COMMIT": "502a447d7e593d638ea45518f2a5e4d4827f683f",
    }
    return db, key_file, env


def _lease_holder(db: Path) -> str | None:
    with sqlite3.connect(db) as connection:
        row = connection.execute(
            "SELECT holder_id FROM service_leases WHERE lease_name = 'stockwatcher-worker'"
        ).fetchone()
    return None if row is None else str(row[0])


def test_duplicate_worker_second_exits_without_scanning(tmp_path: Path) -> None:
    db, _, env = _prepare(tmp_path)
    code = (
        "import time; from stock_watcher.server.worker import main; "
        "import sys; sys.exit(main())"
    )
    first = subprocess.Popen(
        [sys.executable, "-c", code],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    try:
        deadline = time.monotonic() + 30
        holder = None
        while time.monotonic() < deadline:
            holder = _lease_holder(db)
            if holder:
                break
            if first.poll() is not None:
                stderr = first.stderr.read() if first.stderr is not None else ""
                raise AssertionError(f"first worker exited early: {stderr}")
            time.sleep(0.5)
        assert holder, "first worker never acquired the lease"
        first_holder = holder
        # Second worker must fail the lease and exit 0 without scanning.
        second = subprocess.run(
            [sys.executable, "-c", code],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(Path(__file__).resolve().parents[1]),
        )
        assert second.returncode == 0, second.stdout + second.stderr
        assert "safe exit without scanning" in second.stdout or "safe exit" in second.stderr
        # Lease still belongs to the first holder with the same fencing token.
        with sqlite3.connect(db) as connection:
            row = connection.execute(
                "SELECT holder_id, fencing_token FROM service_leases "
                "WHERE lease_name = 'stockwatcher-worker'"
            ).fetchone()
        assert row == (first_holder, 1), row
    finally:
        first.terminate()
        try:
            first.wait(timeout=15)
        except subprocess.TimeoutExpired:
            first.kill()


def test_worker_lease_heartbeat_refreshes(tmp_path: Path) -> None:
    """A running worker renews its lease (heartbeat) continuously."""
    db, _, env = _prepare(tmp_path)
    code = (
        "import time; from stock_watcher.server.worker import main; "
        "import sys; sys.exit(main())"
    )
    first = subprocess.Popen(
        [sys.executable, "-c", code],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if _lease_holder(db):
                break
            time.sleep(0.5)
        heartbeat_a = None
        with sqlite3.connect(db) as connection:
            heartbeat_a = connection.execute(
                "SELECT heartbeat_at FROM service_leases "
                "WHERE lease_name = 'stockwatcher-worker'"
            ).fetchone()[0]
        time.sleep(7)  # longer than the 5s heartbeat interval
        with sqlite3.connect(db) as connection:
            heartbeat_b = connection.execute(
                "SELECT heartbeat_at FROM service_leases "
                "WHERE lease_name = 'stockwatcher-worker'"
            ).fetchone()[0]
        assert heartbeat_b != heartbeat_a, "lease heartbeat did not refresh"
    finally:
        first.terminate()
        try:
            first.wait(timeout=15)
        except subprocess.TimeoutExpired:
            first.kill()


def test_worker_lease_heartbeat_survives_slow_business_tick(tmp_path: Path) -> None:
    """A slow automatic tick must not let the unique-worker lease expire."""
    db, _, env = _prepare(tmp_path)
    code = (
        "import time; import stock_watcher.server.worker as worker; "
        "worker.WorkerRuntime._tick = lambda self: time.sleep(12); "
        "import sys; sys.exit(worker.main())"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", code],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if _lease_holder(db):
                break
            time.sleep(0.5)
        with sqlite3.connect(db) as connection:
            heartbeat_a = connection.execute(
                "SELECT heartbeat_at FROM service_leases "
                "WHERE lease_name = 'stockwatcher-worker'"
            ).fetchone()[0]
        time.sleep(7)
        with sqlite3.connect(db) as connection:
            heartbeat_b = connection.execute(
                "SELECT heartbeat_at FROM service_leases "
                "WHERE lease_name = 'stockwatcher-worker'"
            ).fetchone()[0]
        assert heartbeat_b != heartbeat_a, "slow tick blocked the lease heartbeat"
        assert process.poll() is None, "worker exited during a slow business tick"
    finally:
        process.terminate()
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
