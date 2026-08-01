from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from stock_watcher.domain import SHANGHAI, HealthState
from stock_watcher.providers.tdxquant import TdxFailureReason, TdxTransportError
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
from stock_watcher.ui.tdx_session import TdxDiagnosticSession, TqConnectionState


def test_ui_snapshot_exposes_replay_fields_and_blocks_alerts_when_unhealthy() -> None:
    healthy = demo_batch(datetime(2026, 7, 23, 9, 45, tzinfo=SHANGHAI))
    view = snapshot_from_batch(healthy, health=HealthState.HEALTHY)
    assert len(view.candidates) == 3
    assert view.candidates[0].price > 0
    assert view.candidates[0].change_pct > 0
    assert view.candidates[0].reasons
    assert view.fund_label == "资金未确认"
    assert view.alert_allowed
    assert view.overall_label == "本轮整体偏弱"
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
        "当前表现",
        "短线动能",
        "板块表现",
        "成交与趋势",
        "资金情况",
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
    assert session.connection_state is TqConnectionState.DISCONNECTED
    assert session.data_gate_label == "已阻断"
    assert session.candidate_gate_label == "关闭"
    assert session.status_issues == ("TQ 本机服务：TQ 本机服务不可达",)
    assert not view.alert_allowed
    assert view.candidates == ()
    assert "Mock" not in view.source_label


def test_tdx_diagnostic_ui_reuses_verified_terminal_without_duplicate_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    terminal = tmp_path / "官方 终端" / "TdxW.exe"
    calls: list[dict[str, object]] = []
    passing = PreflightReport(
        status=CheckStatus.PASS,
        platform="Windows",
        python_version="3.12",
        endpoint="http://127.0.0.1:17709/",
        checks=tuple(
            PreflightCheck(name, CheckStatus.PASS, "通过")
            for name in (
                "operating_system",
                "python",
                "terminal_install",
                "python_client",
                "tq_service",
                "api_session",
            )
        ),
        windows_live_verified=True,
    )

    def preflight(**kwargs: object) -> PreflightReport:
        calls.append(kwargs)
        return passing

    monkeypatch.setattr("stock_watcher.ui.tdx_session.run_preflight", preflight)
    session = TdxDiagnosticSession(
        tmp_path / "tdx.sqlite3",
        passing.endpoint,
        terminal_path=terminal,
        preflight_verified=True,
    )

    assert calls == []
    assert session.state is HealthState.WARMING
    assert session.connection_state is TqConnectionState.CONNECTED
    assert session.data_gate_label == "未就绪"
    assert session.candidate_gate_label == "关闭"
    assert any("分钟历史" in issue for issue in session.status_issues)
    assert any("源时间戳" in issue for issue in session.status_issues)
    assert any("M0" in issue for issue in session.status_issues)
    assert session.batch is None
    session.recover()
    assert calls == [
        {
            "endpoint": passing.endpoint,
            "terminal_path": terminal,
        }
    ]
    assert session.state is HealthState.WARMING
    assert session.connection_state is TqConnectionState.CONNECTED


def test_tdx_manual_fetch_uses_normalized_read_only_list_and_keeps_candidates_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    terminal = tmp_path / "官方 终端" / "TdxW.exe"
    fixed_now = datetime(2026, 7, 28, 10, 5, 6, tzinfo=SHANGHAI)
    calls: list[tuple[str, dict[str, object]]] = []

    class RecordingTransport:
        def call(self, method: str, params: dict[str, object]) -> object:
            calls.append((method, params))
            return ("600000.SH",)

    def transport(endpoint: str, timeout_seconds: float) -> RecordingTransport:
        assert endpoint == "http://127.0.0.1:17709/"
        assert timeout_seconds == 5.0
        return RecordingTransport()

    monkeypatch.setattr("stock_watcher.ui.tdx_session.TdxHttpTransport", transport)
    session = TdxDiagnosticSession(
        tmp_path / "tdx.sqlite3",
        "http://127.0.0.1:17709/",
        terminal_path=terminal,
        preflight_verified=True,
        clock=lambda: fixed_now,
    )

    session.begin_manual_fetch()
    assert str(session.connection_state) == TqConnectionState.CHECKING.value
    session.manual_fetch()

    assert calls == [
        (
            "get_stock_list",
            {"market": "5", "list_type": 0},
        )
    ]
    assert type(calls[0][1]["list_type"]) is int
    assert str(session.connection_state) == TqConnectionState.CONNECTED.value
    assert session.state is HealthState.WARMING
    assert session.batch is None
    assert session.last_fetch_at == fixed_now
    assert session.last_connection_check == fixed_now
    assert "成功" in session.last_fetch_detail
    assert "未显示、未保存" in session.last_fetch_detail
    assert session.candidate_gate_label == "关闭"


def test_tdx_manual_fetch_redacts_transport_detail_and_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    terminal = tmp_path / "官方 终端" / "TdxW.exe"

    class FailingTransport:
        def call(self, _method: str, _params: dict[str, object]) -> object:
            raise TdxTransportError(TdxFailureReason.TIMEOUT, "sensitive vendor detail")

    monkeypatch.setattr(
        "stock_watcher.ui.tdx_session.TdxHttpTransport",
        lambda _endpoint, timeout_seconds: FailingTransport(),
    )
    session = TdxDiagnosticSession(
        tmp_path / "tdx.sqlite3",
        "http://127.0.0.1:17709/",
        terminal_path=terminal,
        preflight_verified=True,
    )

    session.manual_fetch()

    assert session.connection_state is TqConnectionState.DISCONNECTED
    assert session.state is HealthState.STOPPED
    assert session.batch is None
    assert session.candidate_gate_label == "关闭"
    assert "响应超时" in session.last_fetch_detail
    assert "sensitive vendor detail" not in session.last_fetch_detail
    assert "sensitive vendor detail" not in " ".join(session.status_issues)


def test_verified_tdx_ui_requires_terminal_path(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="official terminal path"):
        TdxDiagnosticSession(
            tmp_path / "tdx.sqlite3",
            "http://127.0.0.1:17709/",
            preflight_verified=True,
        )
