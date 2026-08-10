"""Service-layer tests: lease/fencing, outbox, commands, secrets, migration."""
from __future__ import annotations

import base64
import os
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from stock_watcher.domain import SHANGHAI
from stock_watcher.services import (
    CommandService,
    CommandStatus,
    CommandType,
    EventOutbox,
    LeaseAcquireError,
    LeaseConfig,
    SecretService,
    WorkerLease,
    WrongMasterKeyError,
)
from stock_watcher.services.secret_service import MASTER_KEY_BYTES, fingerprint
from stock_watcher.storage import SQLiteStore


def make_store(tmp_path: Path) -> SQLiteStore:
    store = SQLiteStore(tmp_path / "test.sqlite3")
    store.initialize()
    return store


def make_lease(store: SQLiteStore, holder: str, *, now: datetime | None = None) -> WorkerLease:
    return WorkerLease(
        store,
        source_commit="502a447d7e593d638ea45518f2a5e4d4827f683f",
        holder_id=holder,
        clock=lambda: now or datetime(2026, 8, 7, 9, 30, tzinfo=SHANGHAI),
    )


def test_lease_acquire_renew_release(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    lease = make_lease(store, "holder-a")
    lease.acquire()
    assert lease.held and lease.fencing_token == 1
    lease.renew()
    status = lease.status()
    assert status["held"] and status["holder_id"] == "holder-a"
    lease.release()
    assert not lease.status()["held"]


def test_second_worker_cannot_acquire_live_lease(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    first = make_lease(store, "holder-a")
    first.acquire()
    second = make_lease(store, "holder-b")
    with pytest.raises(LeaseAcquireError):
        second.acquire()


def test_expired_lease_is_stolen_with_fencing_bump(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    now = datetime(2026, 8, 7, 9, 30, tzinfo=SHANGHAI)
    first = make_lease(store, "holder-a", now=now)
    first.acquire()
    # Old holder stops heartbeating; 30s later TTL(20s) has passed.
    later = now + timedelta(seconds=30)
    second = WorkerLease(
        store,
        source_commit="x" * 40,
        holder_id="holder-b",
        config=LeaseConfig(ttl_seconds=20.0),
        clock=lambda: later,
    )
    second.acquire()
    assert second.fencing_token == 2
    # Old holder's renew must fail (lease lost).
    with pytest.raises(LeaseLostError):
        first.renew()


from stock_watcher.services.worker_lease import LeaseLostError  # noqa: E402


def test_lease_lost_blocks_business_writes(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    now = datetime(2026, 8, 7, 9, 30, tzinfo=SHANGHAI)
    first = make_lease(store, "holder-a", now=now)
    first.acquire()
    later = now + timedelta(seconds=30)
    WorkerLease(
        store,
        source_commit="x" * 40,
        holder_id="holder-b",
        config=LeaseConfig(ttl_seconds=20.0),
        clock=lambda: later,
    ).acquire()
    store.bind_write_guard(first.assert_owned)
    with pytest.raises(LeaseLostError):
        with store.transaction() as connection:
            connection.execute("INSERT INTO notes (key, value) VALUES ('stale', 'write')")
    with store.connect() as connection:
        assert connection.execute("SELECT value FROM notes WHERE key = 'stale'").fetchone() is None


def test_outbox_append_read_since_and_prune(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    outbox = EventOutbox(store, source_commit="a" * 40)
    with store.transaction() as connection:
        first = outbox.append(
            connection,
            event_type="candidates.updated",
            payload={"snapshot_id": 1},
            source_kind="snapshot",
            source_id="1",
        )
        second = outbox.append(
            connection,
            event_type="alert.created",
            payload={"alert_id": 9},
            source_kind="alert",
            source_id="9",
        )
    assert first == 1 and second == 2
    assert outbox.latest_id() == 2
    events = outbox.read_since(1)
    assert [e["event_id"] for e in events] == [2]
    assert events[0]["payload"] == {"alert_id": 9}
    assert outbox.minimum_available_id() == 1
    pruned = outbox.prune(now=datetime(2026, 8, 7, tzinfo=SHANGHAI))
    assert pruned >= 0


def test_outbox_dedupe_source_id(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    outbox = EventOutbox(store, source_commit="a" * 40)
    with store.transaction() as connection:
        outbox.append(
            connection,
            event_type="alert.created",
            payload={"alert_id": 1},
            source_kind="alert",
            source_id="1",
        )
    with pytest.raises(sqlite3.IntegrityError):
        with store.transaction() as connection:
            outbox.append(
                connection,
                event_type="alert.created",
                payload={"alert_id": 1},
                source_kind="alert",
                source_id="1",
            )
    assert outbox.latest_id() == 1


def test_outbox_keeps_every_command_status_transition(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    outbox = EventOutbox(store, source_commit="a" * 40)
    for status in ("queued", "running", "succeeded"):
        outbox.append_own(
            event_type="command.updated",
            payload={"command_id": "cmd-1", "status": status},
            source_kind="command",
            source_id="cmd-1",
        )
    assert [row["payload"]["status"] for row in outbox.read_since(0)] == [
        "queued",
        "running",
        "succeeded",
    ]


def test_command_create_claim_complete(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    seed_user(store)
    commands = CommandService(store)
    created = commands.create(
        command_type=CommandType.MANUAL_REFRESH,
        requested_by=1,
    )
    assert created["status"] == "queued"
    claimed = commands.claim_next(holder_id="worker-1", fencing_token=1)
    assert claimed is not None
    assert claimed["command_id"] == created["command_id"]
    assert claimed["command_id"] == created["command_id"]
    assert claimed["status"] == "running"
    assert claimed["attempts"] == 1
    ok = commands.complete(
        str(claimed["command_id"]),
        holder_id="worker-1",
        fencing_token=1,
        expected_attempt=1,
        status=CommandStatus.SUCCEEDED,
        result={"rounds": 3},
    )
    assert ok
    saved = commands.get(str(claimed["command_id"]))
    assert saved is not None
    assert saved["status"] == "succeeded"
    assert saved["result"] == {"rounds": 3}


def test_command_wrong_holder_cannot_complete(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    seed_user(store)
    commands = CommandService(store)
    created = commands.create(command_type=CommandType.UNIVERSE_REFRESH, requested_by=1)
    commands.claim_next(holder_id="worker-1", fencing_token=1)
    ok = commands.complete(
        str(created["command_id"]),
        holder_id="worker-2",
        fencing_token=2,
        expected_attempt=1,
        status=CommandStatus.SUCCEEDED,
    )
    assert not ok
    running = commands.get(str(created["command_id"]))
    assert running is not None
    assert running["status"] == "running"


def test_manual_refresh_coalescing(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    seed_user(store)
    commands = CommandService(store)
    first = commands.create(command_type=CommandType.MANUAL_REFRESH, requested_by=1)
    second = commands.create(command_type=CommandType.MANUAL_REFRESH, requested_by=2)
    assert second["coalesced"] is True
    assert second["command_id"] == first["command_id"]
    assert first["command_id"] == first["command_id"]


def test_command_has_queued_ignores_terminal_commands(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    seed_user(store)
    commands = CommandService(store)
    created = commands.create(command_type=CommandType.MANUAL_REFRESH, requested_by=1)
    assert commands.has_queued()
    claimed = commands.claim_next(holder_id="worker-1", fencing_token=1)
    assert claimed is not None
    assert not commands.has_queued()
    assert commands.complete(
        str(created["command_id"]),
        holder_id="worker-1",
        fencing_token=1,
        expected_attempt=1,
        status=CommandStatus.FAILED,
        error_code="test",
    )


def test_command_crash_recovery_requeues_then_fails(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    seed_user(store)
    commands = CommandService(store)
    now = datetime(2026, 8, 7, 9, 30, tzinfo=SHANGHAI)
    created = commands.create(
        command_type=CommandType.MANUAL_REFRESH,
        requested_by=1,
        expires_at=now + timedelta(seconds=30),
    )
    commands.claim_next(holder_id="worker-1", fencing_token=1, now=now)
    cursor = now
    # Worker crashed; each retry gets a fresh deadline, so advance well past
    # the per-attempt timeout before expiring.
    for _ in range(2):
        cursor = cursor + timedelta(minutes=6)
        transitions = commands.expire_stale(now=cursor)
        assert any(
            t["command_id"] == created["command_id"] and t["status"] == "queued"
            for t in transitions
        )
        claimed = commands.claim_next(
            holder_id="worker-1", fencing_token=1, now=cursor
        )
        assert claimed is not None
    cursor = cursor + timedelta(minutes=6)
    commands.expire_stale(now=cursor)
    final = commands.get(str(created["command_id"]))
    assert final is not None
    assert final["status"] == "failed"
    assert final["error_code"] == "timeout"


def test_old_command_attempt_cannot_complete_retried_attempt(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    seed_user(store)
    now = datetime(2026, 8, 7, 9, 30, tzinfo=SHANGHAI)
    commands = CommandService(store)
    created = commands.create(
        command_type=CommandType.UNIVERSE_REFRESH,
        requested_by=1,
        expires_at=now + timedelta(seconds=1),
    )
    first = commands.claim_next(holder_id="worker-1", fencing_token=1, now=now)
    assert first is not None and first["attempts"] == 1
    retry_at = now + timedelta(minutes=11)
    commands.expire_stale(now=retry_at)
    second = commands.claim_next(holder_id="worker-1", fencing_token=1, now=retry_at)
    assert second is not None and second["attempts"] == 2
    assert not commands.complete(
        str(created["command_id"]),
        holder_id="worker-1",
        fencing_token=1,
        expected_attempt=1,
        status=CommandStatus.SUCCEEDED,
    )
    assert commands.complete(
        str(created["command_id"]),
        holder_id="worker-1",
        fencing_token=1,
        expected_attempt=2,
        status=CommandStatus.SUCCEEDED,
    )


def make_master_key() -> bytes:
    return os.urandom(MASTER_KEY_BYTES)


def seed_user(store: SQLiteStore, user_id: int = 1) -> None:
    """Insert a minimal web_users row so secret FKs resolve in unit tests."""
    with store.transaction() as connection:
        connection.execute(
            "INSERT INTO web_users (user_id, username, password_hash, role, active, "
            "created_at, updated_at, password_changed_at) "
            "VALUES (?, ?, ?, 'admin', 1, ?, ?, ?)",
            (
                user_id,
                f"unit-{user_id}",
                "x" * 97,
                "2026-08-07T00:00:00+08:00",
                "2026-08-07T00:00:00+08:00",
                "2026-08-07T00:00:00+08:00",
            ),
        )


def test_secret_roundtrip_and_fingerprint(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    seed_user(store)
    secrets = SecretService(store, master_key=make_master_key())
    request = secrets.create_request(
        candidate_token="token-abc-123",
        purpose="token_test",
        requested_by=1,
    )
    assert request["fingerprint"] == fingerprint("token-abc-123")
    plaintext, purpose = secrets.consume_request(request["request_id"])
    assert plaintext == "token-abc-123"
    assert purpose == "token_test"


def test_secret_wrong_key_fails_closed(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    seed_user(store)
    secrets = SecretService(store, master_key=make_master_key())
    request = secrets.create_request(
        candidate_token="token-abc-123",
        purpose="token_test",
        requested_by=1,
    )
    other = SecretService(store, master_key=make_master_key())
    with pytest.raises(WrongMasterKeyError):
        other.consume_request(request["request_id"])


def test_secret_activation_keeps_previous(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    seed_user(store)
    secrets = SecretService(store, master_key=make_master_key())
    secrets.store_active(token="token-old", updated_by=1)
    assert secrets.active_token() == "token-old"
    secrets.store_active(token="token-new", updated_by=1)
    assert secrets.active_token() == "token-new"
    assert secrets.previous_token() == "token-old"
    assert secrets.active_fingerprint() == fingerprint("token-new")


def test_third_secret_rotation_replaces_old_previous_slot(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    seed_user(store)
    secrets = SecretService(store, master_key=make_master_key())
    for token in ("token-one", "token-two", "token-three"):
        secrets.store_active(token=token, updated_by=1)
    assert secrets.active_token() == "token-three"
    assert secrets.previous_token() == "token-two"


def test_secret_request_expiry_and_prune(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    seed_user(store)
    secrets = SecretService(store, master_key=make_master_key())
    request = secrets.create_request(
        candidate_token="token-abc",
        purpose="token_test",
        requested_by=1,
    )
    from datetime import datetime as _dt
    from datetime import timedelta as _td

    from stock_watcher.services.secret_service import SecretServiceError

    later = _dt.now(SHANGHAI) + _td(minutes=30)
    assert secrets.expire_requests(now=later) >= 0
    with pytest.raises(SecretServiceError):
        secrets.consume_request(request["request_id"])


def test_master_key_file_loading(tmp_path: Path) -> None:
    from stock_watcher.services.secret_service import load_master_key

    key = os.urandom(MASTER_KEY_BYTES)
    encoded = base64.urlsafe_b64encode(key).decode("ascii")
    path = tmp_path / "master.key"
    path.write_text(encoded)
    assert load_master_key(path) == key
    bad = tmp_path / "bad.key"
    bad.write_text("too short")
    with pytest.raises(Exception):
        load_master_key(bad)


def test_migration_v6_to_v8_preserves_data(tmp_path: Path) -> None:
    import stock_watcher.storage.sqlite as sqlite_module

    path = tmp_path / "v6.sqlite3"
    original = sqlite_module.SQLiteStore.CURRENT_SCHEMA_VERSION
    sqlite_module.SQLiteStore.CURRENT_SCHEMA_VERSION = 6
    try:
        SQLiteStore(path).initialize()
    finally:
        sqlite_module.SQLiteStore.CURRENT_SCHEMA_VERSION = original
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO notes (key, value) VALUES ('sentinel', 'kept')"
        )
        connection.execute(
            "INSERT INTO schema_version VALUES (6, '2026-08-07T00:00:00Z')"
        )
    SQLiteStore(path).initialize()
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT version FROM schema_version ORDER BY rowid DESC LIMIT 1"
        ).fetchone() == (8,)
        assert connection.execute(
            "SELECT value FROM notes WHERE key = 'sentinel'"
        ).fetchone() == ("kept",)
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        for table in (
            "web_users",
            "web_sessions",
            "web_user_state",
            "service_leases",
            "web_commands",
            "secret_requests",
            "encrypted_secrets",
            "web_events",
            "web_public_state",
            "web_audit_log",
        ):
            assert table in tables
    assert path.with_suffix(".sqlite3.pre-v7.bak").is_file()


def test_migration_idempotent(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.initialize()
    store.initialize()
    with store.connect() as connection:
        assert connection.execute(
            "SELECT version FROM schema_version ORDER BY rowid DESC LIMIT 1"
        ).fetchone() == (8,)


def test_migration_v7_to_v8_relaxes_only_command_event_dedupe(tmp_path: Path) -> None:
    import stock_watcher.storage.sqlite as sqlite_module

    path = tmp_path / "v7.sqlite3"
    original = sqlite_module.SQLiteStore.CURRENT_SCHEMA_VERSION
    sqlite_module.SQLiteStore.CURRENT_SCHEMA_VERSION = 7
    try:
        SQLiteStore(path).initialize()
    finally:
        sqlite_module.SQLiteStore.CURRENT_SCHEMA_VERSION = original
    SQLiteStore(path).initialize()
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT version FROM schema_version ORDER BY rowid DESC LIMIT 1"
        ).fetchone() == (8,)
        index_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'index' "
            "AND name = 'idx_web_events_source_dedupe'"
        ).fetchone()
    assert index_sql is not None
    assert "event_type <> 'command.updated'" in str(index_sql[0])


def test_migration_rollback_via_backup(tmp_path: Path) -> None:
    import stock_watcher.storage.sqlite as sqlite_module

    path = tmp_path / "v6.sqlite3"
    original = sqlite_module.SQLiteStore.CURRENT_SCHEMA_VERSION
    sqlite_module.SQLiteStore.CURRENT_SCHEMA_VERSION = 6
    try:
        SQLiteStore(path).initialize()
    finally:
        sqlite_module.SQLiteStore.CURRENT_SCHEMA_VERSION = original
    with sqlite3.connect(path) as connection:
        connection.execute("INSERT INTO notes (key, value) VALUES ('keep', 'yes')")
    SQLiteStore(path).initialize()  # migrate to v7 (creates .pre-v7.bak)
    backup = path.with_suffix(".sqlite3.pre-v7.bak")
    assert backup.is_file()
    # Simulate a failed upgrade by restoring the pre-v7 backup into a copy.
    rolled = tmp_path / "rolled.sqlite3"
    with sqlite3.connect(backup) as source, sqlite3.connect(rolled) as target:
        source.backup(target)
    SQLiteStore(rolled).initialize()
    with sqlite3.connect(rolled) as connection:
        assert connection.execute(
            "SELECT value FROM notes WHERE key = 'keep'"
        ).fetchone() == ("yes",)


def test_sqlite_concurrent_writers(tmp_path: Path) -> None:
    """Web reads/session writes alongside worker scan commits must not deadlock."""
    store = make_store(tmp_path)
    errors: list[Exception] = []

    def writer(tag: str) -> None:
        try:
            for index in range(20):
                with store.transaction() as connection:
                    connection.execute(
                        "INSERT INTO web_audit_log "
                        "(occurred_at, action, outcome, detail_json) "
                        "VALUES (?, ?, 'succeeded', '{}')",
                        (f"2026-08-07T09:30:{index:02d}+08:00", tag),
                    )
        except Exception as error:  # pragma: no cover
            errors.append(error)

    threads = [threading.Thread(target=writer, args=(f"t{i}",)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    with store.connect() as connection:
        count = connection.execute("SELECT COUNT(*) FROM web_audit_log").fetchone()
    assert count == (80,)
