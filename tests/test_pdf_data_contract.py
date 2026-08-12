from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from stock_watcher.runtime.post_close_pdf import render_post_close_pdf
from stock_watcher.runtime.post_close_report_model import (
    LocalFallbackReport,
    build_local_fallback_report,
    manifest_is_current,
    write_local_fallback_artifacts,
)
from stock_watcher.storage import SQLiteStore

SUMMARY = {
    "trade_date": "2026-08-06",
    "generated_at": "2026-08-06T15:30:03.179232+08:00",
    "alert_count": 5,
    "top_sectors": [["黄金", 3], ["小金属", 3], ["铅锌", 2]],
    "repeated_candidates": [["盛达资源", 2], ["浩通科技", 2]],
    "closing_performance": [
        {"code": "000506.SZ", "name": "招金黄金", "alert_price": 16.58},
        {"code": "000603.SZ", "name": "盛达资源", "alert_price": 32.07},
        {"code": "002428.SZ", "name": "云南锗业", "alert_price": 90.98},
    ],
    "fund_summary": "资金未确认，本日未把资金状态作为盘中增强依据。",
    "health_summary": (
        "最长无扫描间隔90分8秒（11:30→13:00，位于非交易时段）；"
        "进程重启5次；09:45提醒succeeded；14:45提醒succeeded；"
        "15:30总结running；盘中强异动提醒3批；概念缓存：已加载"
    ),
    "summary_text": (
        "今日共形成 5 次观察提醒，重点板块为黄金、小金属、铅锌；"
        "盛达资源、浩通科技。资金未确认，本日未把资金状态作为盘中增强依据。"
        "最长无扫描间隔90分8秒（11:30→13:00，位于非交易时段）；"
        "进程重启5次；09:45提醒succeeded；14:45提醒succeeded；"
        "15:30总结running；盘中强异动提醒3批；概念缓存：已加载"
    ),
    "version": "daily-summary-local-fallback-v1",
}


@pytest.fixture()
def live_fixture_db(tmp_path: Path) -> tuple[SQLiteStore, dict[str, object]]:
    live = Path.home() / "Library/Application Support/StockWatcher/data/stock-watcher.sqlite3"
    if not live.is_file():
        pytest.skip(f"8-06 live SQLite fixture is unavailable: {live}")
    db_path = tmp_path / "fixture.sqlite3"
    with sqlite3.connect(live) as source, sqlite3.connect(db_path) as target:
        source.backup(target)
    return SQLiteStore(db_path), dict(SUMMARY)


def test_local_fallback_real_fixture_uses_1445_top3_and_successful_timeline(
    live_fixture_db: tuple[SQLiteStore, dict[str, object]],
    tmp_path: Path,
) -> None:
    store, summary = live_fixture_db
    report = write_local_fallback_artifacts(
        store,
        summary,
        reports_dir=tmp_path / "reports",
        now=datetime.fromisoformat(str(summary["generated_at"])),
        source_commit_value="b84c0bd04a41eaed343ebf7e99ecaab4998a921e",
    )

    assert report.top3_source == "scheduled_14_45"
    assert [candidate.name for candidate in report.top3] == [
        "杭华股份",
        "锦盛新材",
        "大富科技",
    ]
    fixed = {alert.trigger_type: alert for alert in report.alerts}
    assert fixed["scheduled-09:45"].state == "succeeded"
    assert fixed["scheduled-14:45"].state == "succeeded"
    assert "15:30总结running" not in report.summary_text
    assert "未分类" not in " ".join(candidate.sector or "" for candidate in report.top3)

    pdf = tmp_path / "reports/2026-08-06-A股盘后回顾.pdf"
    source = tmp_path / "reports/2026-08-06-local-summary.json"
    source_record = json.loads(source.read_text(encoding="utf-8"))
    assert source_record["summary_text"] == report.summary_text
    assert source_record["health_summary"] == report.continuity
    assert "15:30总结running" not in source_record["summary_text"]
    assert "15:30总结running" not in source_record["health_summary"]
    assert pdf.read_bytes().count(b"/Type /Page\n") == 2
    if shutil.which("pdftotext") is None:
        pytest.skip("pdftotext is unavailable; semantic PDF text extraction is skipped")
    text = subprocess.check_output(
        ["pdftotext", "-layout", str(pdf), "-"], text=True
    )

    assert "A股盘后本地运行总结（盘后增强数据未取得）" in text
    assert "09:45固定提醒" in text and "成功" in text
    assert "14:45固定提醒" in text and "成功" in text
    assert "杭华股份" in text and "锦盛新材" in text and "大富科技" in text
    assert "未取得完整盘后全市场统计" in text
    assert "15:30总结running" not in text
    assert "未记录" not in text


def test_local_fallback_source_update_invalidates_manifest(
    live_fixture_db: tuple[SQLiteStore, dict[str, object]],
    tmp_path: Path,
) -> None:
    store, summary = live_fixture_db
    reports_dir = tmp_path / "reports"
    write_local_fallback_artifacts(
        store,
        summary,
        reports_dir=reports_dir,
        now=datetime.fromisoformat(str(summary["generated_at"])),
        source_commit_value="b84c0bd04a41eaed343ebf7e99ecaab4998a921e",
    )
    source = reports_dir / "2026-08-06-local-summary.json"
    pdf = reports_dir / "2026-08-06-A股盘后回顾.pdf"
    assert manifest_is_current(
        pdf,
        source_path=source,
        report_mode="local_fallback",
        source_version="daily-summary-local-fallback-v2",
        source_generated_at=str(summary["generated_at"]),
        source_commit_value="b84c0bd04a41eaed343ebf7e99ecaab4998a921e",
    )
    source.write_text(source.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert not manifest_is_current(
        pdf,
        source_path=source,
        report_mode="local_fallback",
        source_version="daily-summary-local-fallback-v2",
        source_generated_at=str(summary["generated_at"]),
        source_commit_value="b84c0bd04a41eaed343ebf7e99ecaab4998a921e",
    )


def test_manifest_renderer_version_change_invalidates_pdf(
    live_fixture_db: tuple[SQLiteStore, dict[str, object]],
    tmp_path: Path,
) -> None:
    store, summary = live_fixture_db
    reports_dir = tmp_path / "reports"
    write_local_fallback_artifacts(
        store,
        summary,
        reports_dir=reports_dir,
        now=datetime.fromisoformat(str(summary["generated_at"])),
        source_commit_value="b84c0bd04a41eaed343ebf7e99ecaab4998a921e",
    )
    source = reports_dir / "2026-08-06-local-summary.json"
    pdf = reports_dir / "2026-08-06-A股盘后回顾.pdf"
    manifest = pdf.with_name(f"{pdf.name}.meta.json")
    value = manifest.read_text(encoding="utf-8").replace(
        '"renderer_version": "local-fallback-brief-v1"',
        '"renderer_version": "old-renderer"',
    )
    manifest.write_text(value, encoding="utf-8")
    assert not manifest_is_current(
        pdf,
        source_path=source,
        report_mode="local_fallback",
        source_version="daily-summary-local-fallback-v2",
        source_generated_at=str(summary["generated_at"]),
        source_commit_value="b84c0bd04a41eaed343ebf7e99ecaab4998a921e",
    )


def test_full_renderer_rejects_local_summary_and_never_uses_closing_performance(
    tmp_path: Path,
) -> None:
    local_record = dict(SUMMARY)
    local_record.update(
        {
            "report_mode": "local_fallback",
            "market": {"securities": 1},
            "market_segments": [],
            "top_sectors": [],
            "top3": [{"code": "x", "name": "x"}] * 3,
            "source_coverage": {"local": True},
            "data_limitations": [],
        }
    )
    with pytest.raises(ValueError, match="cannot render local_fallback"):
        render_post_close_pdf(local_record, tmp_path / "wrong.pdf")


def test_incomplete_full_renderer_record_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="missing required fields"):
        render_post_close_pdf(dict(SUMMARY), tmp_path / "wrong-incomplete.pdf")


def test_local_model_round_trip_preserves_explicit_mode() -> None:
    report = LocalFallbackReport.from_record(
        {
            "local_fallback_report": {
                "trade_date": "2026-08-06",
                "generated_at": "2026-08-06T15:30:03+08:00",
                "report_mode": "local_fallback",
                "source_version": "daily-summary-local-fallback-v2",
                "source_generated_at": "2026-08-06T15:30:03+08:00",
                "source_commit": "b84c0bd",
                "alert_count": 0,
                "top3": [],
                "alerts": [],
                "scan_count": 1,
                "healthy_scan_count": 1,
                "runtime_session_count": 1,
                "restart_count": 0,
                "sleep_count": 0,
                "wake_count": 0,
                "concept_status": "已加载",
                "continuity": "扫描轮数1轮",
                "market_limitation": "未取得完整盘后全市场统计",
                "fund_summary": "资金未确认",
                "summary_text": "本地总结",
            }
        }
    )
    assert report.report_mode == "local_fallback"
    assert report.top3 == ()


def test_local_fallback_reports_trading_gap_even_when_lunch_is_longer(
    tmp_path: Path,
) -> None:
    store = SQLiteStore(tmp_path / "continuity.sqlite3")
    lunch_end = datetime.fromisoformat("2026-08-06T13:00:05+08:00")
    timestamps = [datetime.fromisoformat("2026-08-06T11:30:17+08:00")]
    timestamps.extend(lunch_end + timedelta(seconds=30 * index) for index in range(112))
    timestamps.extend(
        [
            datetime.fromisoformat("2026-08-06T13:55:58+08:00"),
            datetime.fromisoformat("2026-08-06T14:38:11+08:00"),
        ]
    )
    for completed_at in timestamps:
        store.record_scan_run(
            {
                "started_at": completed_at.isoformat(),
                "completed_at": completed_at.isoformat(),
                "trigger_type": "automatic",
                "health": "HEALTHY",
                "detail": "正常",
                "audit_json": "{}",
            }
        )

    summary = {
        "trade_date": "2026-08-06",
        "generated_at": "2026-08-06T15:30:03+08:00",
        "health_summary": "最长无扫描间隔90分8秒（午休）",
        "summary_text": "本地总结",
    }
    report = build_local_fallback_report(
        store,
        summary,
        now=datetime.fromisoformat("2026-08-06T15:30:03+08:00"),
        source_commit_value="rc4-test",
    )

    assert "最长无扫描间隔1小时29分48秒" in report.continuity
    assert "最长交易时段无扫描间隔42分13秒" in report.continuity
    assert "13:55:58→14:38:11" in report.continuity
    assert "交易时段超90秒空窗1段" in report.continuity
    assert "90分8秒" not in report.continuity
    assert "最长交易时段无扫描间隔42分13秒" in report.summary_text
    assert "90分8秒" not in report.summary_text
