from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from stock_watcher.engine import PostCloseReview, build_post_close_review
from stock_watcher.providers.tushare.models import Record, TransportResult

from .post_close_pdf import prune_post_close_reports, render_post_close_pdf


class PostCloseDataProvider(Protocol):
    def stock_list(self, **params: str | int | float | bool) -> TransportResult: ...

    def trading_dates(
        self,
        **params: str | int | float | bool,
    ) -> TransportResult: ...

    def daily_bars(self, **params: str | int | float | bool) -> TransportResult: ...

    def moneyflow(self, **params: str | int | float | bool) -> TransportResult: ...

    def adjustment_factors(
        self,
        **params: str | int | float | bool,
    ) -> TransportResult: ...


@dataclass(frozen=True, slots=True)
class PostCloseReviewCollection:
    review: PostCloseReview
    stock_record_count: int
    daily_record_counts: dict[str, int]
    moneyflow_record_count: int
    open_dates_checked: int
    mechanical_jump_exclusions: int
    optional_failures: tuple[str, ...]
    retrospective_only: bool = False

    def source_coverage(self) -> dict[str, Any]:
        return {
            "stock_records": self.stock_record_count,
            "daily_records": self.daily_record_counts,
            "moneyflow_records": self.moneyflow_record_count,
            "open_dates_checked": self.open_dates_checked,
            "mechanical_jump_exclusions": self.mechanical_jump_exclusions,
            "optional_failures": list(self.optional_failures),
        }

    def as_record(self) -> dict[str, Any]:
        record = self.review.as_record()
        record["title"] = f"{self.review.trade_date} A股盘后回顾"
        record["report_type"] = "post_close_review"
        record["source_coverage"] = self.source_coverage()
        if self.retrospective_only:
            record["verdict"] = "RETROSPECTIVE_ONLY"
            limitations = record.get("data_limitations")
            existing = list(limitations) if isinstance(limitations, list) else []
            record["data_limitations"] = [
                *existing,
                "Primary普通Pro限流；本次仅使用已授权Super静态高级诊断兜底，不构成主路线或盘中Live验收。",
            ]
        return record


def collect_post_close_review(
    provider: PostCloseDataProvider,
    *,
    trade_date: date,
    generated_at: datetime,
) -> PostCloseReviewCollection:
    """Fetch one bounded close-data set and build the deterministic market review."""
    compact = trade_date.strftime("%Y%m%d")
    stocks = provider.stock_list(exchange="", list_status="L")
    target_daily = provider.daily_bars(trade_date=compact)

    optional_failures: list[str] = []
    open_dates: tuple[date, ...] = (trade_date,)
    try:
        calendar = provider.trading_dates(
            exchange="SSE",
            start_date=(trade_date - timedelta(days=30)).strftime("%Y%m%d"),
            end_date=compact,
            is_open="1",
        )
        parsed_open_dates = tuple(
            sorted(
                {
                    parsed
                    for row in calendar.records
                    if (parsed := _compact_date(row.get("cal_date"))) is not None
                    and _truthy(row.get("is_open"))
                }
            )
        )
        if trade_date in parsed_open_dates:
            open_dates = parsed_open_dates
        else:
            optional_failures.append("trade_calendar")
    except Exception:
        optional_failures.append("trade_calendar")

    review_dates = open_dates[-4:]
    daily_by_date: dict[date, tuple[Record, ...]] = {
        trade_date: target_daily.records,
    }
    for day in review_dates:
        if day == trade_date:
            continue
        try:
            daily_by_date[day] = provider.daily_bars(
                trade_date=day.strftime("%Y%m%d")
            ).records
        except Exception:
            optional_failures.append(f"daily:{day.isoformat()}")
            break

    moneyflow_records: tuple[Record, ...] = ()
    try:
        moneyflow_records = provider.moneyflow(trade_date=compact).records
    except Exception:
        optional_failures.append("moneyflow")

    previous_adjustments: tuple[Record, ...] = ()
    current_adjustments: tuple[Record, ...] = ()
    if len(review_dates) >= 2:
        try:
            previous_adjustments = provider.adjustment_factors(
                trade_date=review_dates[-2].strftime("%Y%m%d")
            ).records
            current_adjustments = provider.adjustment_factors(
                trade_date=compact
            ).records
        except Exception:
            optional_failures.append("adjustment_factor")
    else:
        optional_failures.append("adjustment_factor")

    mechanical_codes = _mechanical_jump_codes(
        previous_adjustments,
        current_adjustments,
    )
    review = build_post_close_review(
        trade_date=trade_date,
        generated_at=generated_at,
        stock_records=stocks.records,
        daily_records_by_date=daily_by_date,
        open_dates=open_dates,
        moneyflow_records=moneyflow_records,
        mechanical_jump_codes=frozenset(mechanical_codes),
    )
    return PostCloseReviewCollection(
        review=review,
        stock_record_count=len(stocks.records),
        daily_record_counts={
            day.isoformat(): len(rows) for day, rows in daily_by_date.items()
        },
        moneyflow_record_count=len(moneyflow_records),
        open_dates_checked=len(open_dates),
        mechanical_jump_exclusions=len(mechanical_codes),
        optional_failures=tuple(optional_failures),
    )


def application_summary_record(
    collection: PostCloseReviewCollection,
    *,
    alert_count: int,
    health_interruption_count: int,
) -> dict[str, Any]:
    review = collection.review
    market = review.market
    top3_names = "、".join(candidate.name for candidate in review.top3)
    top_sector_names = "、".join(sector.name for sector in review.top_sectors[:3])
    market_tone = _market_tone(market.up_ratio, market.median_change_pct)
    interruption_copy = (
        "数据运行未记录中断。"
        if health_interruption_count == 0
        else f"记录到 {health_interruption_count} 次数据延迟或中断。"
    )
    coverage_copy = (
        f"收盘日线覆盖 {market.securities} 只；"
        f"上涨 {market.advances} 只，下跌 {market.declines} 只。"
    )
    sector_copy = top_sector_names or "无满足板块硬门的行业"
    return {
        "trade_date": review.trade_date,
        "generated_at": review.generated_at,
        "alert_count": alert_count,
        "top_sectors": [
            [sector.name, sector.strong_count]
            for sector in review.top_sectors[:3]
        ],
        "repeated_candidates": [
            [candidate.name, 1] for candidate in review.top3
        ],
        "closing_performance": [
            {
                "code": candidate.code,
                "name": candidate.name,
                "close_price": candidate.close,
                "change_pct": candidate.change_pct,
                "sector": candidate.sector,
                "level": candidate.level,
                "reasons": list(candidate.reasons),
            }
            for candidate in review.top3
        ],
        "fund_summary": review.fund_summary,
        "health_summary": f"{coverage_copy}{interruption_copy}",
        "summary_text": (
            f"{review.trade_date} A股收盘{market_tone}，"
            f"全市场上涨比例 {market.up_ratio:.1%}，"
            f"涨跌幅中位数 {market.median_change_pct:+.2f}%。"
            f"重点行业为{sector_copy}；"
            f"按收盘行情、板块宽度和前三日背景形成盘后观察Top3：{top3_names}。"
            f"今日自动观察提醒 {alert_count} 次；手动查看不计入提醒限额。"
            f"{review.fund_summary}"
        ),
        "version": (
            "daily-summary-retrospective-v1"
            if collection.retrospective_only
            else "daily-summary-market-review-v1"
        ),
    }


def write_post_close_report(
    collection: PostCloseReviewCollection,
    *,
    reports_dir: Path,
    alert_count: int,
    health_interruption_count: int,
    alert_timeline: Sequence[Mapping[str, object]] = (),
) -> tuple[Path, Path]:
    """Atomically write credential-free JSON, Markdown, and fixed three-page PDF."""
    reports_dir.mkdir(parents=True, exist_ok=True)
    trade_date = collection.review.trade_date
    stem = f"{trade_date}-A股盘后回顾"
    json_path = reports_dir / f"{stem}.json"
    markdown_path = reports_dir / f"{stem}.md"
    pdf_path = reports_dir / f"{stem}.pdf"
    record = collection.as_record()
    record["intraday_alert_count"] = alert_count
    record["health_interruption_count"] = health_interruption_count
    record["alert_timeline"] = list(alert_timeline)
    _atomic_write(
        json_path,
        json.dumps(record, ensure_ascii=False, indent=2) + "\n",
    )
    _atomic_write(
        markdown_path,
        render_post_close_markdown(
            collection,
            alert_count=alert_count,
            health_interruption_count=health_interruption_count,
        ),
    )
    _atomic_render_pdf(record, pdf_path)
    prune_post_close_reports(
        reports_dir,
        reference_date=date.fromisoformat(str(trade_date)),
    )
    return json_path, markdown_path


def alert_timeline_records(
    history: Sequence[Mapping[str, object]],
) -> tuple[dict[str, object], ...]:
    """Reduce saved alert rows to the small, credential-free timeline used in reports."""
    records: list[dict[str, object]] = []
    for row in sorted(history, key=lambda item: str(item.get("displayed_at", ""))):
        trigger_type = str(row.get("trigger_type", ""))
        if trigger_type not in {"scheduled-09:45", "scheduled-14:45", "intraday"}:
            continue
        names: list[str] = []
        payload = row.get("payload_json")
        try:
            parsed = json.loads(payload) if isinstance(payload, str) else {}
        except json.JSONDecodeError:
            parsed = {}
        if isinstance(parsed, dict):
            candidates = parsed.get("candidates")
            if isinstance(candidates, list):
                names = [
                    str(candidate.get("name", candidate.get("code", "")))
                    for candidate in candidates[:3]
                    if isinstance(candidate, dict)
                    and str(candidate.get("name", candidate.get("code", "")))
                ]
        records.append(
            {
                "displayed_at": str(row.get("displayed_at", "")),
                "trigger_type": trigger_type,
                "candidate_names": names,
            }
        )
    return tuple(records)


def render_post_close_markdown(
    collection: PostCloseReviewCollection,
    *,
    alert_count: int,
    health_interruption_count: int,
) -> str:
    review = collection.review
    market = review.market
    lines = [
        f"# {review.trade_date} A股盘后回顾",
        "",
        "## 市场整体",
        "",
        (
            f"- 覆盖 {market.securities} 只：上涨 {market.advances}，"
            f"下跌 {market.declines}，平盘 {market.unchanged}"
        ),
        f"- 上涨比例 {market.up_ratio:.1%}，涨跌幅中位数 {market.median_change_pct:+.2f}%",
        f"- 涨幅达到 2% 的股票 {market.strong_count} 只",
        f"- 今日自动观察提醒 {alert_count} 次；手动查看不计入提醒限额",
        "",
        "## 强势行业",
        "",
    ]
    if review.top_sectors:
        lines.extend(
            (
                f"- {sector.name}：上涨 {sector.up_ratio:.1%}，"
                f"明显走强 {sector.strong_count} 只，中位涨幅 "
                f"{sector.median_change_pct:+.2f}%"
            )
            for sector in review.top_sectors[:10]
        )
    else:
        lines.append("- 无满足板块硬门的行业")
    lines.extend(["", "## 盘后观察 Top3", ""])
    for candidate in review.top3:
        lines.extend(
            [
                (
                    f"### {candidate.rank}. {candidate.name} "
                    f"({candidate.code}) · {candidate.level}"
                ),
                "",
                (
                    f"- 收盘价 ¥{candidate.close:.2f}，"
                    f"当日涨跌 {candidate.change_pct:+.2f}%"
                ),
                f"- 行业：{candidate.sector}",
                f"- 依据：{'；'.join(candidate.reasons)}",
                "",
            ]
        )
    lines.extend(
        [
            "## 资金与数据说明",
            "",
            f"- {review.fund_summary}",
            (
                "- 数据运行未记录中断"
                if health_interruption_count == 0
                else f"- 记录到 {health_interruption_count} 次数据延迟或中断"
            ),
        ]
    )
    lines.extend(f"- {limitation}" for limitation in review.data_limitations)
    lines.extend(
        [
            "",
            "> 本报告是只读观察回顾，不构成交易建议，也不连接交易账户。",
            "",
        ]
    )
    return "\n".join(lines)


def _market_tone(up_ratio: float, median_change_pct: float) -> str:
    if up_ratio >= 0.55 and median_change_pct > 0:
        return "整体偏强"
    if up_ratio <= 0.45 and median_change_pct < 0:
        return "整体偏弱"
    return "整体分化"


def _compact_date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value.replace("-", ""), "%Y%m%d").date()
    except ValueError:
        return None


def _truthy(value: object) -> bool:
    return str(value).casefold() in {"1", "true", "y", "yes"}


def _mechanical_jump_codes(
    previous: tuple[Record, ...],
    current: tuple[Record, ...],
) -> set[str]:
    previous_values = {
        str(row.get("ts_code")): _number(row.get("adj_factor"))
        for row in previous
        if row.get("ts_code")
    }
    return {
        code
        for row in current
        if (code := str(row.get("ts_code") or ""))
        and code in previous_values
        and _number(row.get("adj_factor")) != previous_values[code]
    }


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return 0.0
    try:
        return float(value)
    except ValueError:
        return 0.0


def _atomic_write(path: Path, content: str) -> None:
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(content, encoding="utf-8", newline="\n")
    temporary.replace(path)


def _atomic_render_pdf(record: Mapping[str, Any], path: Path) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        render_post_close_pdf(record, temporary)
        temporary.replace(path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
