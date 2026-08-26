"""Repeat-occurrence tracker, persistence, history projection and backfill."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from stock_watcher.domain import SHANGHAI, HealthState
from stock_watcher.engine import AlertTrigger, Candidate
from stock_watcher.engine.candidates import CandidateBatch
from stock_watcher.runtime.repeat_tracker import (
    ACTIVATE_AT,
    CandidateRepeatTracker,
    RepeatProjection,
    compute_repeat_state,
    format_repeat_label,
)
from stock_watcher.services.public_state import PublicStateBuilder
from stock_watcher.services.stockwatcher_service import StockWatcherService
from stock_watcher.storage import SQLiteStore


def _at(day: int, hour: int = 10, minute: int = 0) -> datetime:
    return datetime(2026, 8, day, hour, minute, tzinfo=SHANGHAI)


def make_batch(
    source_ts: datetime,
    *,
    prefix: str = "测",
    health: HealthState = HealthState.HEALTHY,
    provider_version: str = "tushare-15000",
    codes: tuple[str, str, str] = ("600001.SH", "600002.SH", "600003.SH"),
) -> CandidateBatch:
    candidates = tuple(
        Candidate(
            code=codes[index],
            name=f"{prefix}{index + 1}",
            sector="测试板块",
            sector_code="TEST",
            level="强" if index == 0 else "中",
            score=50.0 - index,
            price_score=20.0,
            sector_score=20.0,
            trend_score=10.0,
            penalty=0.0,
            reasons=("测试",),
            source_ts=source_ts,
            provider_version=provider_version,
            config_version="test-config",
            app_version="test-app",
            price=10.0 + index,
            change_pct=float(index + 1),
            total_score=50.0 - index,
            core_score=40.0,
            is_formal=index < 2,
            is_supplement=index >= 2,
        )
        for index in range(3)
    )
    return CandidateBatch(
        source_ts=source_ts,
        generated_at=source_ts,
        health=health,
        overall_weak=False,
        candidates=candidates,
    )


def persist(
    store: SQLiteStore,
    batch: CandidateBatch,
    seen_at: datetime,
    source_type: str = "automatic",
) -> int:
    tracker = CandidateRepeatTracker(store)
    with store.transaction() as connection:
        snapshot_id = store.record_batch_in(connection, batch)
        tracker.observe_batch_in(
            connection,
            batch=batch,
            snapshot_id=snapshot_id,
            seen_at=seen_at,
            source_type=source_type,
        )
    return snapshot_id


def projection(store: SQLiteStore, code: str) -> RepeatProjection:
    return CandidateRepeatTracker(store).projections_from_store([code])[code]


def day_row(store: SQLiteStore, code: str, trade_date: str) -> dict[str, object]:
    with store.connect() as connection:
        row = connection.execute(
            "SELECT count_after, span_days_after, active_after, source_types_json "
            "FROM candidate_repeat_days WHERE code = ? AND trade_date = ?",
            (code, trade_date),
        ).fetchone()
    assert row is not None
    return {
        "count_after": row[0],
        "span_days_after": row[1],
        "active_after": row[2],
        "source_types": json.loads(str(row[3])),
    }


def test_format_label_and_fourteenth_day_window() -> None:
    assert format_repeat_label(12, 3) == "近12天第3次"
    dates = [
        datetime(2026, 8, 1, tzinfo=SHANGHAI).date(),
        datetime(2026, 8, 6, tzinfo=SHANGHAI).date(),
        datetime(2026, 8, 14, tzinfo=SHANGHAI).date(),
    ]
    state = compute_repeat_state(dates)
    assert state.active is True
    assert state.occurrence_count == ACTIVATE_AT
    assert state.span_days == 14


def test_same_code_same_day_scanned_100_times_counts_once(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "repeat.sqlite3")
    store.initialize()
    first = _at(10, 9, 35)
    for index in range(100):
        moment = first + timedelta(minutes=index)
        persist(store, make_batch(moment), moment)
    state = projection(store, "600001.SH")
    assert state.occurrence_count == 1
    assert state.active is False
    with store.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM candidate_repeat_days").fetchone() == (3,)


def test_same_day_realtime_fixed_and_intraday_merge_to_one(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "merge.sqlite3")
    store.initialize()
    now = _at(10, 10, 0)
    batch = make_batch(now)
    snapshot_id = persist(store, batch, now, "automatic")
    tracker = CandidateRepeatTracker(store)
    with store.transaction() as connection:
        tracker.note_source_in(
            connection,
            batch=batch,
            snapshot_id=snapshot_id,
            seen_at=_at(10, 10, 20),
            source_type="intraday",
        )
        tracker.note_source_in(
            connection,
            batch=batch,
            snapshot_id=snapshot_id,
            seen_at=_at(10, 14, 45),
            source_type="scheduled-14:45",
        )
    state = projection(store, "600001.SH")
    assert state.occurrence_count == 1
    sources = day_row(store, "600001.SH", "2026-08-10")["source_types"]
    assert sources == ["automatic", "intraday", "scheduled-14:45"]


def test_leave_and_reenter_same_day_still_once(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "reenter.sqlite3")
    store.initialize()
    persist(store, make_batch(_at(10, 10, 0), prefix="早"), _at(10, 10, 0))
    persist(
        store,
        make_batch(
            _at(10, 11, 0),
            prefix="午",
            codes=("601001.SH", "601002.SH", "601003.SH"),
        ),
        _at(10, 11, 0),
    )
    persist(store, make_batch(_at(10, 14, 0), prefix="晚"), _at(10, 14, 0))
    assert projection(store, "600001.SH").occurrence_count == 1


def test_three_appearances_same_day_do_not_activate(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "same-day.sqlite3")
    store.initialize()
    day = _at(10)
    persist(store, make_batch(day), day, "automatic")
    persist(store, make_batch(day + timedelta(hours=1)), day + timedelta(hours=1), "intraday")
    persist(
        store,
        make_batch(day + timedelta(hours=4)),
        day + timedelta(hours=4),
        "scheduled-14:45",
    )
    state = projection(store, "600001.SH")
    assert state.occurrence_count == 1
    assert state.active is False


def test_three_trade_days_within_14_calendar_days_activate(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "activate.sqlite3")
    store.initialize()
    persist(store, make_batch(_at(1)), _at(1))
    persist(store, make_batch(_at(6)), _at(6))
    persist(store, make_batch(_at(12)), _at(12))
    state = projection(store, "600001.SH")
    assert state.active is True
    assert state.occurrence_count == 3
    assert state.span_days == 12
    assert state.label == "近12天第3次"
    first = day_row(store, "600001.SH", "2026-08-01")
    second = day_row(store, "600001.SH", "2026-08-06")
    third = day_row(store, "600001.SH", "2026-08-12")
    assert first["active_after"] == 0
    assert second["active_after"] == 0
    assert third["active_after"] == 1


def test_third_on_day_14_activates_and_day_15_drops_expired_start(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "window.sqlite3")
    store.initialize()
    persist(store, make_batch(_at(1)), _at(1))
    persist(store, make_batch(_at(6)), _at(6))
    persist(store, make_batch(_at(14)), _at(14))
    assert projection(store, "600001.SH").active is True

    other = SQLiteStore(tmp_path / "expired.sqlite3")
    other.initialize()
    persist(other, make_batch(_at(1)), _at(1))
    persist(other, make_batch(_at(6)), _at(6))
    persist(other, make_batch(_at(15)), _at(15))
    late = projection(other, "600001.SH")
    assert late.active is False
    assert late.occurrence_count == 2
    assert late.sequence_started_on is not None
    assert late.sequence_started_on.isoformat() == "2026-08-06"
    persist(other, make_batch(_at(19)), _at(19))
    activated = projection(other, "600001.SH")
    assert activated.active is True
    assert activated.occurrence_count == 3
    assert activated.sequence_started_on is not None
    assert activated.sequence_started_on.isoformat() == "2026-08-06"
    assert day_row(other, "600001.SH", "2026-08-01")["active_after"] == 0
    assert day_row(other, "600001.SH", "2026-08-06")["count_after"] == 2


def test_expired_dates_are_not_wiped_from_history(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "keep.sqlite3")
    store.initialize()
    persist(store, make_batch(_at(1)), _at(1))
    persist(store, make_batch(_at(6)), _at(6))
    persist(store, make_batch(_at(15)), _at(15))
    with store.connect() as connection:
        dates = [
            row[0]
            for row in connection.execute(
                "SELECT trade_date FROM candidate_repeat_days WHERE code = '600001.SH' "
                "ORDER BY trade_date"
            )
        ]
    assert dates == ["2026-08-01", "2026-08-06", "2026-08-15"]


def test_activation_is_permanent_and_later_days_increment(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "permanent.sqlite3")
    store.initialize()
    persist(store, make_batch(_at(1)), _at(1))
    persist(store, make_batch(_at(6)), _at(6))
    persist(store, make_batch(_at(12)), _at(12))
    persist(store, make_batch(_at(20)), _at(20))
    persist(store, make_batch(_at(20, 14, 45)), _at(20, 14, 45))
    state = projection(store, "600001.SH")
    assert state.active is True
    assert state.occurrence_count == 4
    assert state.span_days == 20
    assert state.label == "近20天第4次"
    september = datetime(2026, 9, 10, 10, tzinfo=SHANGHAI)
    persist(store, make_batch(september), september)
    later = projection(store, "600001.SH")
    assert later.active is True
    assert later.occurrence_count == 5
    with store.connect() as connection:
        tracker = CandidateRepeatTracker(store)
        fourth = tracker.historical_fields_for(
            connection, code="600001.SH", trade_date=_at(20).date()
        )
        third = tracker.historical_fields_for(
            connection, code="600001.SH", trade_date=_at(12).date()
        )
    assert fourth["repeat_active"] is True
    assert fourth["repeat_count"] == 4
    assert fourth["repeat_activated_at"] == third["repeat_activated_at"]


def test_warming_stale_stopped_and_mock_do_not_count(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "quality.sqlite3")
    store.initialize()
    persist(store, make_batch(_at(1), health=HealthState.WARMING), _at(1))
    persist(store, make_batch(_at(2), health=HealthState.STALE), _at(2))
    persist(store, make_batch(_at(3), health=HealthState.STOPPED), _at(3))
    persist(store, make_batch(_at(4), provider_version="mock-provider"), _at(4))
    persist(store, make_batch(_at(5), provider_version="replay-v1"), _at(5))
    persist(store, make_batch(_at(6), provider_version="synthetic"), _at(6))
    with store.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM candidate_repeat_days").fetchone() == (0,)


def test_service_persist_and_restart_keep_state(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "service.sqlite3")
    store.initialize()
    first = StockWatcherService(store, clock=lambda: _at(1))
    first.batch = make_batch(_at(1))
    first.state = HealthState.HEALTHY
    first._persist_scan_snapshot(_at(1))  # noqa: SLF001
    first.batch = make_batch(_at(6))
    first._persist_scan_snapshot(_at(6))  # noqa: SLF001
    first.batch = make_batch(_at(12))
    first._persist_scan_snapshot(_at(12))  # noqa: SLF001

    restarted = StockWatcherService(store, clock=lambda: _at(20))
    restarted.batch = make_batch(_at(20))
    restarted.state = HealthState.HEALTHY
    restarted._persist_scan_snapshot(_at(20))  # noqa: SLF001
    payload = restarted._candidate_payload(restarted.batch)  # noqa: SLF001
    target = next(row for row in payload if row["code"] == "600001.SH")
    assert target["repeat_active"] is True
    assert target["repeat_count"] == 4
    assert target["repeat_label"] == "近20天第4次"
    state = PublicStateBuilder(store).build(now=_at(20))
    assert state["candidates"][0]["repeat_active"] is True
    with store.connect() as connection:
        updated = json.loads(
            connection.execute(
                "SELECT payload_json FROM web_events WHERE event_type = 'candidates.updated' "
                "ORDER BY event_id DESC LIMIT 1"
            ).fetchone()[0]
        )
    assert updated["candidates"][0]["repeat_active"] is True
    assert updated["candidates"][0]["repeat_label"] == "近20天第4次"


def test_alert_payload_uses_snapshot_candidates_and_does_not_double_count(
    tmp_path: Path,
) -> None:
    store = SQLiteStore(tmp_path / "alert.sqlite3")
    store.initialize()
    service = StockWatcherService(store, clock=lambda: _at(12))
    service.batch = make_batch(_at(1))
    service.state = HealthState.HEALTHY
    service._persist_scan_snapshot(_at(1))  # noqa: SLF001
    service.batch = make_batch(_at(6))
    service._persist_scan_snapshot(_at(6))  # noqa: SLF001
    service.batch = make_batch(_at(12))
    snapshot_id = service._persist_scan_snapshot(_at(12))  # noqa: SLF001
    service._record_alert(  # noqa: SLF001
        _at(12),
        AlertTrigger.INTRADAY,
        "strong",
        "盘中强异动",
        "个股与板块同步增强｜资金未确认",
        snapshot_id=snapshot_id,
    )
    assert projection(store, "600001.SH").occurrence_count == 3
    with store.connect() as connection:
        payload = json.loads(
            connection.execute(
                "SELECT payload_json FROM web_events WHERE event_type = 'alert.created'"
            ).fetchone()[0]
        )
    assert payload["trigger_type"] == "intraday"
    assert payload["candidates"][0]["repeat_active"] is True
    assert payload["candidates"][0]["repeat_label"] == "近12天第3次"
    assert payload["snapshot_id"] == snapshot_id


def test_backfill_is_idempotent_and_preserves_historical_purple(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "backfill.sqlite3")
    store.initialize()
    store.record_batch(make_batch(_at(1)))
    store.record_batch(make_batch(_at(6)))
    store.record_batch(make_batch(_at(12)))
    tracker = CandidateRepeatTracker(store)
    first = tracker.backfill()
    second = tracker.backfill()
    assert first == second
    assert first.occurrences == 9
    assert first.activated == 3
    with store.connect() as connection:
        days = connection.execute(
            "SELECT trade_date, active_after, count_after FROM candidate_repeat_days "
            "WHERE code = '600001.SH' ORDER BY trade_date"
        ).fetchall()
    assert days == [
        ("2026-08-01", 0, 1),
        ("2026-08-06", 0, 2),
        ("2026-08-12", 1, 3),
    ]
    with store.connect() as connection:
        first_day = tracker.historical_fields_for(
            connection, code="600001.SH", trade_date=_at(1).date()
        )
        third_day = tracker.historical_fields_for(
            connection, code="600001.SH", trade_date=_at(12).date()
        )
    assert first_day["repeat_active"] is False
    assert first_day["repeat_label"] is None
    assert third_day["repeat_active"] is True
    assert third_day["repeat_label"] == "近12天第3次"
    purple = store.query_snapshots(limit=20, repeat_active=True)
    assert {row["source_ts"][:10] for row in purple} == {"2026-08-12"}


def test_v9_to_v10_migration_adds_repeat_tables_and_backup(tmp_path: Path) -> None:
    import sqlite3
    from contextlib import closing

    import stock_watcher.storage.sqlite as sqlite_module

    path = tmp_path / "v9.sqlite3"
    original = sqlite_module.SQLiteStore.CURRENT_SCHEMA_VERSION
    sqlite_module.SQLiteStore.CURRENT_SCHEMA_VERSION = 9
    try:
        SQLiteStore(path).initialize()
    finally:
        sqlite_module.SQLiteStore.CURRENT_SCHEMA_VERSION = original
    SQLiteStore(path).initialize()
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute(
            "SELECT version FROM schema_version ORDER BY rowid DESC LIMIT 1"
        ).fetchone() == (10,)
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert "candidate_repeat_days" in tables
        assert "candidate_repeat_states" in tables
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    assert path.with_suffix(".sqlite3.pre-v10.bak").is_file()


def test_scoring_ranking_and_strong_move_modules_do_not_import_repeat_tracker() -> None:
    root = Path(__file__).resolve().parents[1] / "src" / "stock_watcher" / "engine"
    for name in ("candidates.py", "stable_top3.py", "alerts.py"):
        text = (root / name).read_text(encoding="utf-8")
        assert "repeat_tracker" not in text
        assert "repeat_active" not in text
        assert "CandidateRepeatTracker" not in text
