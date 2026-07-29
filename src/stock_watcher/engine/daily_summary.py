from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class DailySummary:
    trade_date: str
    generated_at: str
    alert_count: int
    top_sectors: tuple[tuple[str, int], ...]
    repeated_candidates: tuple[tuple[str, int], ...]
    closing_performance: tuple[dict[str, str | float], ...]
    fund_summary: str
    health_summary: str
    summary_text: str
    version: str

    def as_record(self) -> dict[str, Any]:
        return asdict(self)


class DailySummaryEngine:
    """Produces a deterministic 15:30 review without prediction or tuning."""

    def generate(
        self,
        *,
        trade_date: date,
        generated_at: datetime,
        alert_history: list[dict[str, Any]],
        closing_prices: dict[str, float] | None = None,
        health_interruption_count: int = 0,
        version: str = "daily-summary-v1",
    ) -> DailySummary:
        closing_prices = closing_prices or {}
        sectors: Counter[str] = Counter()
        names: dict[str, str] = {}
        appearances: Counter[str] = Counter()
        first_prices: dict[str, float] = {}
        fund_labels: Counter[str] = Counter()
        for alert in alert_history:
            for candidate in _candidates(alert.get("payload_json")):
                code = str(candidate.get("code", ""))
                if not code:
                    continue
                names[code] = str(candidate.get("name", code))
                appearances[code] += 1
                sector = str(candidate.get("sector", ""))
                if sector:
                    sectors[sector] += 1
                fund = str(candidate.get("fund_label", "资金未确认"))
                fund_labels[fund] += 1
                price = candidate.get("price")
                if code not in first_prices and isinstance(price, (int, float)):
                    first_prices[code] = float(price)
        performance: list[dict[str, str | float]] = []
        for code, first_price in sorted(first_prices.items()):
            close = closing_prices.get(code)
            row: dict[str, str | float] = {
                "code": code,
                "name": names.get(code, code),
                "alert_price": round(first_price, 4),
            }
            if close is not None and first_price > 0:
                row["close_price"] = round(close, 4)
                row["change_to_close_pct"] = round(
                    (close / first_price - 1.0) * 100.0,
                    4,
                )
            performance.append(row)
        top_sectors = tuple(sectors.most_common(3))
        repeated = tuple(
            (names.get(code, code), count)
            for code, count in appearances.most_common()
            if count > 1
        )
        fund_summary = (
            "资金未确认，本日未把资金状态作为盘中增强依据。"
            if not fund_labels or set(fund_labels) == {"资金未确认"}
            else "；".join(f"{label} {count}次" for label, count in fund_labels.most_common())
        )
        health_summary = (
            "数据运行正常，未记录中断。"
            if health_interruption_count == 0
            else f"记录到 {health_interruption_count} 次数据延迟或中断。"
        )
        sector_copy = (
            "、".join(name for name, _ in top_sectors)
            if top_sectors
            else "无明确集中板块"
        )
        repeated_copy = (
            "、".join(name for name, _ in repeated)
            if repeated
            else "无多次重复股票"
        )
        summary_text = (
            f"今日共形成 {len(alert_history)} 次观察提醒，重点板块为{sector_copy}；"
            f"{repeated_copy}。{fund_summary}{health_summary}"
        )
        return DailySummary(
            trade_date=trade_date.isoformat(),
            generated_at=generated_at.isoformat(),
            alert_count=len(alert_history),
            top_sectors=top_sectors,
            repeated_candidates=repeated,
            closing_performance=tuple(performance),
            fund_summary=fund_summary,
            health_summary=health_summary,
            summary_text=summary_text,
            version=version,
        )


def _candidates(value: object) -> list[dict[str, object]]:
    if not isinstance(value, str):
        return []
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return []
    rows = payload.get("candidates") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]
