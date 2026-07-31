from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import cast

from stock_watcher.config import DataSourceSettings
from stock_watcher.domain import SHANGHAI
from stock_watcher.providers.tushare import Tushare15000Provider
from stock_watcher.providers.tushare.models import (
    DataQuality,
    ProviderProvenance,
    Record,
    SourceTimestampKind,
    TransportResult,
)
from stock_watcher.runtime import (
    RuntimeUniverse,
    TushareV1Runtime,
    application_summary_record,
    collect_post_close_review,
    write_post_close_report,
)
from stock_watcher.runtime.post_close_pdf import (
    POST_CLOSE_PDF_LAYOUT_VERSION,
    list_post_close_report_dates,
    prune_post_close_reports,
    render_post_close_pdf,
)
from stock_watcher.security import PRIMARY_CREDENTIAL, MemoryCredentialStore
from stock_watcher.ui.tushare_v1_session import TushareV1Session

TRADE_DATE = date(2026, 7, 30)
GENERATED_AT = datetime(2026, 7, 30, 15, 30, tzinfo=SHANGHAI)
OPEN_DATES = (
    date(2026, 7, 27),
    date(2026, 7, 28),
    date(2026, 7, 29),
    TRADE_DATE,
)
CODES = (
    "600001.SH",
    "600002.SH",
    "600003.SH",
    "000001.SZ",
    "000002.SZ",
    "000003.SZ",
)


def transport_result(records: tuple[Record, ...]) -> TransportResult:
    return TransportResult(
        records=records,
        http_status=200,
        elapsed_seconds=0.1,
        provenance=ProviderProvenance(
            provider_profile="test",
            endpoint="test",
            provider_version="test",
            schema_version="test",
            source_ts=GENERATED_AT,
            received_ts=GENERATED_AT,
            source_timestamp_kind=SourceTimestampKind.SUPPLIER,
            freshness_seconds=0.0,
            quality=DataQuality.HEALTHY,
            degraded=False,
            fields_used=(),
        ),
    )


class FakeCloseProvider:
    def __init__(self) -> None:
        self.daily_by_date: dict[str, tuple[Record, ...]] = {
            day.strftime("%Y%m%d"): tuple(
                {
                    "ts_code": code,
                    "trade_date": day.strftime("%Y%m%d"),
                    "open": 10.0 + day_index * 0.1,
                    "high": 10.5 + day_index * 0.1 + code_index * 0.05,
                    "low": 9.9 + day_index * 0.1,
                    "close": 10.3 + day_index * 0.1 + code_index * 0.04,
                    "pre_close": 10.0 + day_index * 0.1,
                    "pct_chg": 5.5 - code_index * 0.4 if day == TRADE_DATE else 1.0,
                    "vol": 1000 + day_index * 100,
                    "amount": 10000 + day_index * 2000 + code_index * 100,
                }
                for code_index, code in enumerate(CODES)
            )
            for day_index, day in enumerate(OPEN_DATES)
        }

    def stock_list(self, **_params: str | int | float | bool) -> TransportResult:
        return transport_result(
            tuple(
                {
                    "ts_code": code,
                    "name": f"样本{index}",
                    "industry": "行业甲" if index <= 3 else "行业乙",
                    "list_date": "20200101",
                }
                for index, code in enumerate(CODES, start=1)
            )
        )

    def trading_dates(
        self,
        **_params: str | int | float | bool,
    ) -> TransportResult:
        return transport_result(
            tuple(
                {
                    "cal_date": day.strftime("%Y%m%d"),
                    "is_open": "1",
                }
                for day in OPEN_DATES
            )
        )

    def daily_bars(
        self,
        **params: str | int | float | bool,
    ) -> TransportResult:
        return transport_result(self.daily_by_date[str(params["trade_date"])])

    def moneyflow(
        self,
        **_params: str | int | float | bool,
    ) -> TransportResult:
        return transport_result(())

    def adjustment_factors(
        self,
        **_params: str | int | float | bool,
    ) -> TransportResult:
        return transport_result(())


class NoScanRuntime:
    def __init__(self) -> None:
        self.universe = RuntimeUniverse(
            profiles=(),
            memberships=(),
            trends={},
            high_3d={},
            open_dates=(TRADE_DATE,),
            concept_loaded=False,
        )

    def scan_once(self) -> None:
        raise AssertionError("15:30 review must not start an after-hours realtime scan")


class FailingCloseProvider(FakeCloseProvider):
    def __init__(self) -> None:
        super().__init__()
        self.stock_list_calls = 0

    def stock_list(self, **_params: str | int | float | bool) -> TransportResult:
        self.stock_list_calls += 1
        raise RuntimeError("simulated provider failure")


class SequenceClock:
    def __init__(self, values: list[datetime]) -> None:
        self.values = values

    def __call__(self) -> datetime:
        return self.values.pop(0)


def test_post_close_collection_writes_full_market_json_and_markdown(
    tmp_path: Path,
) -> None:
    collection = collect_post_close_review(
        FakeCloseProvider(),
        trade_date=TRADE_DATE,
        generated_at=GENERATED_AT,
    )
    summary = application_summary_record(
        collection,
        alert_count=2,
        health_interruption_count=1,
    )
    json_path, markdown_path = write_post_close_report(
        collection,
        reports_dir=tmp_path,
        alert_count=2,
        health_interruption_count=1,
    )

    assert len(collection.review.top3) == 3
    assert summary["version"] == "daily-summary-market-review-v1"
    assert summary["alert_count"] == 2
    assert "上涨" in str(summary["summary_text"])
    assert "盘后观察Top3" in str(summary["summary_text"])
    assert json_path.is_file()
    json_copy = json_path.read_text(encoding="utf-8")
    assert '"title": "2026-07-30 A股盘后回顾"' in json_copy
    assert "盘后回顾测试" not in json_copy
    markdown = markdown_path.read_text(encoding="utf-8")
    assert "# 2026-07-30 A股盘后回顾" in markdown
    assert "## 市场整体" in markdown
    assert "## 盘后观察 Top3" in markdown
    assert "test-token" not in markdown
    pdf_path = tmp_path / "2026-07-30-A股盘后回顾.pdf"
    assert pdf_path.is_file()
    pdf_bytes = pdf_path.read_bytes()
    assert pdf_bytes.startswith(b"%PDF")
    assert pdf_bytes.count(b"/Type /Page\n") == 3


def test_session_generates_full_market_review_automatically_at_1530(
    tmp_path: Path,
) -> None:
    credentials = MemoryCredentialStore()
    credentials.set(PRIMARY_CREDENTIAL, "test-token")
    runtime = NoScanRuntime()
    provider = FakeCloseProvider()

    def factory(
        _settings: DataSourceSettings,
        _store: MemoryCredentialStore,
    ) -> tuple[TushareV1Runtime, Tushare15000Provider]:
        return (
            cast(TushareV1Runtime, runtime),
            cast(Tushare15000Provider, provider),
        )

    session = TushareV1Session(
        tmp_path / "StockWatcher.sqlite3",
        credential_store=credentials,
        runtime_factory=factory,  # type: ignore[arg-type]
        clock=lambda: GENERATED_AT,
    )

    session.recover()

    summary = session.store.get_daily_summary(TRADE_DATE.isoformat())
    assert summary is not None
    assert summary["version"] == "daily-summary-market-review-v1"
    assert summary["alert_count"] == 0
    report = tmp_path / "reports" / "2026-07-30-A股盘后回顾.md"
    assert report.is_file()
    assert "A股盘后回顾" in report.read_text(encoding="utf-8")
    assert (tmp_path / "reports" / "2026-07-30-A股盘后回顾.pdf").is_file()


def test_fixed_pdf_renderer_and_report_retention_are_bounded(tmp_path: Path) -> None:
    assert POST_CLOSE_PDF_LAYOUT_VERSION == "research-brief-v1"
    record = collect_post_close_review(
        FakeCloseProvider(),
        trade_date=TRADE_DATE,
        generated_at=GENERATED_AT,
    ).as_record()
    pdf_path = render_post_close_pdf(
        record,
        tmp_path / "2026-07-30-A股盘后回顾.pdf",
    )
    old = tmp_path / "2026-06-20-A股盘后回顾.pdf"
    old.write_bytes(b"old")
    recent = tmp_path / "2026-07-01-A股盘后回顾.json"
    recent.write_text("{}", encoding="utf-8")
    unrelated = tmp_path / "2026-01-01-not-a-report.pdf"
    unrelated.write_bytes(b"keep")

    removed = prune_post_close_reports(
        tmp_path,
        reference_date=TRADE_DATE,
    )
    dates = list_post_close_report_dates(
        tmp_path,
        reference_date=TRADE_DATE,
    )

    assert pdf_path.is_file()
    assert pdf_path.read_bytes().count(b"/Type /Page\n") == 3
    assert removed == (old,)
    assert not old.exists()
    assert recent.is_file()
    assert unrelated.is_file()
    assert dates == ("2026-07-30", "2026-07-01")


def test_failed_1530_review_retries_no_more_than_once_per_minute(
    tmp_path: Path,
) -> None:
    credentials = MemoryCredentialStore()
    credentials.set(PRIMARY_CREDENTIAL, "test-token")
    runtime = NoScanRuntime()
    provider = FailingCloseProvider()
    clock = SequenceClock(
        [
            GENERATED_AT,
            GENERATED_AT + timedelta(seconds=10),
            GENERATED_AT + timedelta(seconds=61),
        ]
    )
    session = TushareV1Session(
        tmp_path / "retry.sqlite3",
        credential_store=credentials,
        runtime_factory=lambda _settings, _store: (
            cast(TushareV1Runtime, runtime),
            cast(Tushare15000Provider, provider),
        ),
        clock=clock,
    )

    session.recover()
    session.recover()
    session.recover()

    assert provider.stock_list_calls == 2
    assert "盘后回顾暂未生成，将在60秒后自动重试。" in session.status_issues
    assert session.store.get_daily_summary(TRADE_DATE.isoformat()) is None
