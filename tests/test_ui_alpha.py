from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from stock_watcher.domain import SHANGHAI, HealthState
from stock_watcher.providers.tdxquant_preflight import (
    CheckStatus,
    PreflightCheck,
    PreflightReport,
)
from stock_watcher.storage import SQLiteStore
from stock_watcher.ui.demo import demo_batch
from stock_watcher.ui.presenter import (
    detail_reasons,
    format_change,
    format_time,
    snapshot_from_batch,
)
from stock_watcher.ui.tdx_session import TdxDiagnosticSession


def test_ui_snapshot_exposes_replay_fields_and_blocks_alerts_when_unhealthy() -> None:
    healthy = demo_batch(datetime(2026, 7, 23, 9, 45, tzinfo=SHANGHAI))
    view = snapshot_from_batch(healthy, health=HealthState.HEALTHY)
    assert len(view.candidates) == 3
    assert view.candidates[0].price > 0
    assert view.candidates[0].change_pct > 0
    assert view.candidates[0].reasons
    assert view.fund_label == "资金模块：未就绪（M0 未通过）"
    assert view.alert_allowed
    assert view.overall_label == "整体偏弱"
    assert view.previous_candidates == ()

    stopped = snapshot_from_batch(
        healthy,
        health=HealthState.STOPPED,
        health_detail="模拟断开",
    )
    assert not stopped.alert_allowed
    assert stopped.candidates == ()
    assert stopped.previous_candidates == view.candidates
    assert stopped.overall_label == "数据中断"


def test_ui_formats_signed_percentages() -> None:
    assert format_change(2.5) == "+2.50%"
    assert format_change(-1.25) == "-1.25%"
    assert format_time(datetime(2026, 7, 23, 9, 45, tzinfo=SHANGHAI)) == "2026-07-23 09:45"


def test_detail_copy_is_plain_language_and_keeps_internal_fields_hidden() -> None:
    batch = demo_batch(datetime(2026, 7, 23, 9, 45, tzinfo=SHANGHAI))
    row = snapshot_from_batch(batch, health=HealthState.HEALTHY).candidates[0]
    reasons = detail_reasons(row)
    assert [title for title, _ in reasons] == [
        "涨幅明显",
        "涨速较快",
        "板块配合较好",
        "三日走势较稳",
    ]
    copy = " ".join(f"{title} {explanation}" for title, explanation in reasons)
    assert "Provider" not in copy
    assert "M0" not in copy
    assert "买入" not in copy


def test_history_reader_is_query_only_and_returns_visible_batches(tmp_path: Path) -> None:
    path = tmp_path / "demo.sqlite3"
    store = SQLiteStore(path)
    store.initialize()
    batch = demo_batch(datetime(2026, 7, 23, 9, 45, tzinfo=SHANGHAI))
    store.record_batch(batch)

    reader = SQLiteStore(path, read_only=True)
    rows = reader.list_recent_snapshots()
    assert len(rows) == 1
    assert rows[0]["health"] == "HEALTHY"
    assert "candidates" in str(rows[0]["payload_json"])
    assert reader.read_only


def test_ui_demo_does_not_change_candidate_engine_semantics() -> None:
    batch = demo_batch(datetime(2026, 7, 23, 9, 45, tzinfo=SHANGHAI))
    assert [candidate.level for candidate in batch.candidates] == ["强", "中", "近"]
    assert batch.fund_module == "unavailable"


def test_tdx_diagnostic_ui_never_relabels_replay_as_live(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = PreflightReport(
        status=CheckStatus.FAIL,
        platform="macOS fixture",
        python_version="3.12",
        endpoint="http://127.0.0.1:17709/",
        checks=(
            PreflightCheck(
                "tq_service",
                CheckStatus.FAIL,
                "TQ 本机服务不可达",
            ),
        ),
    )
    monkeypatch.setattr("stock_watcher.ui.tdx_session.run_preflight", lambda **_kwargs: report)
    session = TdxDiagnosticSession(tmp_path / "tdx.sqlite3", report.endpoint)
    view = snapshot_from_batch(
        session.batch,
        health=session.state,
        health_detail=session.health_detail,
        source_label=session.source_label,
        phase_label=session.phase_label,
    )
    assert session.batch is None
    assert session.state is HealthState.STOPPED
    assert not view.alert_allowed
    assert view.candidates == ()
    assert "Mock" not in view.source_label
