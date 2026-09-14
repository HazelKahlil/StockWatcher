from __future__ import annotations

import json
import sqlite3
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from approval_test_support import NOW, seed_snapshot, synthetic_database

from stock_watcher.feedback.repository import ApprovalCommand, ApprovalRepository, FeedbackError
from stock_watcher.feedback.schema import (
    TABLE_COLUMNS,
    SchemaUnavailable,
    database,
    install_on_connection,
    migrate_with_backup,
)


@pytest.fixture()
def repository(tmp_path: Path) -> ApprovalRepository:
    return ApprovalRepository(synthetic_database(tmp_path / "app.db"), clock=lambda: NOW)


def command(**changes: Any) -> ApprovalCommand:
    current = ApprovalCommand(1, "300829.SZ", True, uuid.uuid4().hex, 0)
    return replace(current, **changes)


def count(repository: ApprovalRepository, table: str) -> int:
    assert table in TABLE_COLUMNS
    with database(repository.path) as connection:
        return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def test_unacted_is_unreviewed_and_does_not_write(repository: ApprovalRepository) -> None:
    state = repository.snapshot_state(2, 1)
    assert len(state["items"]) == 3
    assert all(r["feedback_status"] == "unreviewed" and r["version"] == 0 for r in state["items"])
    assert count(repository, "web_candidate_approval_events") == 0
    assert count(repository, "web_candidate_approvals") == 0


def test_approve_and_revoke_keep_two_events(repository: ApprovalRepository) -> None:
    approved = repository.apply(2, command())
    assert approved["state"]["selected"] is True
    assert approved["state"]["version"] == 1
    revoked = repository.apply(2, command(selected=False, expected_version=1))
    assert revoked["state"]["feedback_status"] == "withdrawn"
    assert revoked["state"]["first_selected_at"] == approved["state"]["first_selected_at"]
    assert [x["action"] for x in repository.events(2)["items"]] == ["revoke", "approve"]
    assert count(repository, "web_candidate_approvals") == 1


def test_same_request_is_deduplicated_after_later_revoke(repository: ApprovalRepository) -> None:
    original = command()
    repository.apply(2, original)
    repository.apply(2, command(selected=False, expected_version=1))
    retry = repository.apply(2, original)
    assert retry["replayed"] is True
    assert retry["receipt"]["selected"] is True
    assert retry["state"]["selected"] is False
    assert retry["state"]["version"] == 2
    assert count(repository, "web_candidate_approval_events") == 2


def test_request_id_cannot_be_reused_with_different_payload(repository: ApprovalRepository) -> None:
    original = command()
    repository.apply(2, original)
    with pytest.raises(FeedbackError, match="请求标识"):
        repository.apply(2, replace(original, selected=False))


def test_noop_has_receipt_but_not_a_negative_or_another_vote(
    repository: ApprovalRepository,
) -> None:
    result = repository.apply(2, command(selected=False))
    assert not result["receipt"]["changed"]
    assert result["state"]["feedback_status"] == "unreviewed"
    repository.apply(2, command())
    result = repository.apply(2, command(expected_version=1))
    assert not result["receipt"]["changed"]
    assert count(repository, "web_candidate_approval_events") == 1
    assert count(repository, "web_candidate_approval_requests") == 3


def test_accounts_are_isolated_even_with_same_request_id(repository: ApprovalRepository) -> None:
    original = command()
    repository.apply(2, original)
    assert repository.snapshot_state(3, 1)["items"][0]["selected"] is False
    assert repository.events(3)["items"] == []
    repository.apply(3, original)
    assert count(repository, "web_candidate_approval_events") == 2
    assert len(repository.events(1, admin=True)["items"]) == 2
    with pytest.raises(FeedbackError) as error:
        repository.events(2, admin=True)
    assert error.value.status == 403


@pytest.mark.parametrize("user", [0, 4, 999])
def test_inactive_or_unknown_users_cannot_write(repository: ApprovalRepository, user: int) -> None:
    with pytest.raises(FeedbackError) as error:
        repository.apply(user, command())
    assert error.value.status == 401
    assert count(repository, "web_candidate_approval_events") == 0


def test_day_scope_survives_refresh_but_does_not_leak_to_next_day(
    repository: ApprovalRepository,
) -> None:
    repository.apply(2, command())
    with database(repository.path, write=True) as connection, connection:
        seed_snapshot(connection, 2, "2026-09-11T14:45:00+08:00")
        seed_snapshot(connection, 3, "2026-09-12T09:45:00+08:00")
    assert repository.snapshot_state(2, 2)["items"][0]["selected"]
    assert not repository.snapshot_state(2, 3)["items"][0]["selected"]
    assert count(repository, "web_candidate_approval_events") == 1


def test_late_click_binds_old_snapshot_and_is_marked_retrospective(
    repository: ApprovalRepository,
) -> None:
    with database(repository.path, write=True) as connection, connection:
        seed_snapshot(connection, 2, "2026-09-11T14:45:00+08:00")
    repository.apply(2, command())
    item = repository.events(2)["items"][0]
    assert item["snapshot_id"] == 1
    assert item["context"]["retrospective"] is True
    assert item["context"]["snapshot_is_current_at_acceptance"] is False


def test_candidate_context_survives_source_cleanup(repository: ApprovalRepository) -> None:
    original = command()
    repository.apply(2, original)
    with database(repository.path, write=True) as connection, connection:
        connection.execute("DELETE FROM candidate_items")
        connection.execute("DELETE FROM candidate_snapshots")
    event = repository.events(2)["items"][0]
    assert event["context"]["candidate"]["name"] == "金丹科技"
    assert event["context"]["candidate"]["factors"]["price_score"] == 12
    assert "secret" not in json.dumps(event["context"])
    assert repository.apply(2, original)["replayed"]


def test_old_version_cannot_overwrite_new_state(repository: ApprovalRepository) -> None:
    repository.apply(2, command())
    with pytest.raises(FeedbackError) as error:
        repository.apply(2, command(selected=False))
    assert error.value.code == "version_conflict"
    assert repository.snapshot_state(2, 1)["items"][0]["selected"]


def test_two_tabs_only_one_stale_version_wins(repository: ApprovalRepository) -> None:
    def attempt(_: int) -> str:
        try:
            repository.apply(2, command())
            return "ok"
        except FeedbackError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, range(2))) == ["ok", "version_conflict"]
    assert count(repository, "web_candidate_approval_events") == 1


def test_concurrent_retries_of_same_uuid_are_exactly_once(repository: ApprovalRepository) -> None:
    original = command()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: repository.apply(2, original), range(4)))
    assert sum(not x["replayed"] for x in results) == 1
    assert count(repository, "web_candidate_approval_events") == 1


def test_transaction_failure_cannot_leave_partial_vote(repository: ApprovalRepository) -> None:
    with database(repository.path, write=True) as connection, connection:
        connection.execute("""CREATE TRIGGER reject_approval_request
          BEFORE INSERT ON web_candidate_approval_requests BEGIN
          SELECT RAISE(ABORT, 'injected failure'); END""")
    with pytest.raises(sqlite3.IntegrityError):
        repository.apply(2, command())
    assert count(repository, "web_candidate_approval_events") == 0
    assert count(repository, "web_candidate_approvals") == 0


@pytest.mark.parametrize("changes", [
    {"selected": "true"}, {"snapshot_id": True}, {"expected_version": -1},
    {"request_id": "tiny"}, {"code": "x';DELETE"}, {"surface": "trading"},
    {"snapshot_id": 1 << 100}, {"expected_version": 1 << 100},
])
def test_invalid_commands_rejected(
    repository: ApprovalRepository, changes: dict[str, object],
) -> None:
    with pytest.raises(FeedbackError):
        repository.apply(2, command(**changes))
    assert count(repository, "web_candidate_approval_events") == 0


@pytest.mark.parametrize("changes,code", [
    ({"snapshot_id": 999}, "snapshot_not_found"),
    ({"code": "600000.SH"}, "candidate_not_found"),
])
def test_membership_is_verified(
    repository: ApprovalRepository, changes: dict[str, object], code: str,
) -> None:
    with pytest.raises(FeedbackError) as error:
        repository.apply(2, command(**changes))
    assert error.value.code == code


def test_future_and_timezone_missing_snapshots_rejected(repository: ApprovalRepository) -> None:
    with database(repository.path, write=True) as connection, connection:
        connection.execute("UPDATE candidate_snapshots SET source_ts='2099-01-01T00:00:00+08:00'")
    with pytest.raises(FeedbackError):
        repository.apply(2, command())
    with database(repository.path, write=True) as connection, connection:
        connection.execute("UPDATE candidate_snapshots SET source_ts='2026-09-11T10:00:00'")
    with pytest.raises(FeedbackError):
        repository.apply(2, command())


def test_rate_limit_does_not_break_receipt_retry(repository: ApprovalRepository) -> None:
    first = command(selected=False)
    repository.apply(2, first)
    for _ in range(59):
        repository.apply(2, command(selected=False))
    with pytest.raises(FeedbackError) as error:
        repository.apply(2, command())
    assert error.value.status == 429
    assert repository.apply(2, first)["replayed"]


def test_paginated_events_no_duplicates(repository: ApprovalRepository) -> None:
    for i in range(5):
        repository.apply(2, command(selected=i % 2 == 0, expected_version=i))
    one = repository.events(2, limit=2)
    two = repository.events(2, limit=2, cursor=one["next_cursor"])
    three = repository.events(2, limit=2, cursor=two["next_cursor"])
    ids = [x["event_id"] for page in (one, two, three) for x in page["items"]]
    assert len(ids) == len(set(ids)) == 5
    assert three["next_cursor"] is None


def test_no_automatic_schema_creation(tmp_path: Path) -> None:
    path = synthetic_database(tmp_path / "app.db", extension=False)
    with pytest.raises(SchemaUnavailable):
        ApprovalRepository(path).snapshot_state(2, 1)
    with database(path) as connection:
        assert not set(TABLE_COLUMNS) & {
            r[0] for r in connection.execute("SELECT name FROM sqlite_master")
        }


def test_explicit_migration_backs_up_preserves_core_and_is_idempotent(tmp_path: Path) -> None:
    path = synthetic_database(tmp_path / "app.db", extension=False)
    result = migrate_with_backup(path, tmp_path / "backups")
    assert result["changed"]
    backup = Path(str(result["backup"]))
    with database(backup) as connection:
        assert connection.execute("SELECT COUNT(*) FROM candidate_items").fetchone()[0] == 3
        assert not connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='web_candidate_approval_schema'"
        ).fetchone()
    with database(path) as connection:
        assert connection.execute("SELECT version FROM schema_version").fetchone()[0] == 10
        assert connection.execute("SELECT COUNT(*) FROM candidate_items").fetchone()[0] == 3
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert migrate_with_backup(path, tmp_path / "backups")["changed"] is False
    assert len(list((tmp_path / "backups").iterdir())) == 1


def test_partial_schema_is_not_silently_repaired(tmp_path: Path) -> None:
    path = synthetic_database(tmp_path / "app.db", extension=False)
    with database(path, write=True) as connection, connection:
        connection.execute("CREATE TABLE web_candidate_approvals(wrong TEXT)")
    with database(path, write=True) as connection, pytest.raises(SchemaUnavailable):
        install_on_connection(connection)


def test_schema_ddl_rolls_back_on_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from stock_watcher.feedback import schema
    path = synthetic_database(tmp_path / "app.db", extension=False)
    monkeypatch.setattr(schema, "DDL", (*schema.DDL[:2], "INVALID SQL"))
    with database(path, write=True) as connection:
        with pytest.raises(sqlite3.Error):
            install_on_connection(connection)
        assert not set(TABLE_COLUMNS) & {
            r[0] for r in connection.execute("SELECT name FROM sqlite_master")
        }


def test_old_core_can_ignore_extension_without_dropping_feedback(
    repository: ApprovalRepository,
) -> None:
    repository.apply(2, command())
    with database(repository.path) as connection:
        assert connection.execute("SELECT version FROM schema_version").fetchone()[0] == 10
        assert connection.execute("SELECT COUNT(*) FROM candidate_items").fetchone()[0] == 3
    assert len(repository.events(2)["items"]) == 1


def test_historical_events_not_backdated(repository: ApprovalRepository) -> None:
    with database(repository.path, write=True) as connection, connection:
        connection.execute("UPDATE candidate_snapshots SET source_ts='2026-09-10T09:45:00+08:00'")
    repository.apply(2, command(surface="history"))
    event = repository.events(2)["items"][0]
    assert event["recorded_at"] == NOW.isoformat()
    assert event["trade_date"] == "2026-09-10"
    assert event["context"]["retrospective"]
    assert event["context"]["candidate_age_seconds"] > timedelta(days=1).total_seconds()
