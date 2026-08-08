from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date, datetime, time
from statistics import mean, median
from typing import Any

from stock_watcher.domain import (
    SHANGHAI,
    DataQuality,
    RollingFeatures,
    SectorMembership,
    Security,
    SourceTimestampKind,
)

from .sector_engine import SectorEngine

Record = dict[str, str | int | float | bool | None]


@dataclass(frozen=True, slots=True)
class PostCloseCandidate:
    rank: int
    code: str
    name: str
    market: str
    close: float
    change_pct: float
    sector: str
    sector_code: str
    level: str
    core_score: float
    sector_score: float
    close_evidence_score: float
    trend_score: float
    sector_up_ratio: float
    sector_strong_count: int
    sector_relative_strength: float
    amount_ratio_3d: float | None
    trend_3d_pct: float
    daily_fund_background: str
    reasons: tuple[str, ...]
    retrospective_only: bool = True


@dataclass(frozen=True, slots=True)
class PostCloseSector:
    name: str
    member_count: int
    up_ratio: float
    strong_count: int
    median_change_pct: float
    score: float


@dataclass(frozen=True, slots=True)
class PostCloseMarket:
    securities: int
    advances: int
    declines: int
    unchanged: int
    up_ratio: float
    median_change_pct: float
    strong_count: int
    amount_cny: float


@dataclass(frozen=True, slots=True)
class PostCloseReview:
    trade_date: str
    generated_at: str
    title: str
    verdict: str
    market: PostCloseMarket
    market_segments: tuple[dict[str, str | int | float], ...]
    top_sectors: tuple[PostCloseSector, ...]
    top3: tuple[PostCloseCandidate, ...]
    fund_capability: str
    fund_summary: str
    data_limitations: tuple[str, ...]
    provider_route: str = "https://fastapic.stockai888.top"
    raw_payload_persisted: bool = False
    credential_persisted_or_printed: bool = False
    schema_version: str = "stockwatcher-post-close-review-1"

    def as_record(self) -> dict[str, Any]:
        return asdict(self)

    def daily_summary_record(self) -> dict[str, Any]:
        names = "、".join(candidate.name for candidate in self.top3)
        return {
            "trade_date": self.trade_date,
            "generated_at": self.generated_at,
            "alert_count": 0,
            "top_sectors": [
                [sector.name, sector.strong_count]
                for sector in self.top_sectors[:3]
            ],
            "repeated_candidates": [
                [candidate.name, 1] for candidate in self.top3
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
                for candidate in self.top3
            ],
            "fund_summary": self.fund_summary,
            "health_summary": (
                "当日日线覆盖完整；当日分钟历史未形成可用记录，"
                "因此本总结不构成盘中实时提醒证据。"
            ),
            "summary_text": (
                f"这是盘后历史数据回溯测试，不是盘中真实提醒。"
                f"按收盘行情、行业宽度和前三日背景，回溯观察为{names}；"
                f"{self.fund_summary}"
            ),
            "version": "daily-summary-retrospective-v1",
        }


def build_post_close_review(
    *,
    trade_date: date,
    generated_at: datetime,
    stock_records: tuple[Record, ...],
    daily_records_by_date: dict[date, tuple[Record, ...]],
    open_dates: tuple[date, ...],
    moneyflow_records: tuple[Record, ...] = (),
    mechanical_jump_codes: frozenset[str] = frozenset(),
) -> PostCloseReview:
    """Build an honest close-only review without inventing minute momentum."""
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=SHANGHAI)
    else:
        generated_at = generated_at.astimezone(SHANGHAI)
    current_records = daily_records_by_date.get(trade_date, ())
    if not current_records:
        raise ValueError("the selected trade date has no daily records")
    observed_at = datetime.combine(trade_date, time(15, 0), tzinfo=SHANGHAI)
    stocks = _stock_index(stock_records)
    current = {
        code: record
        for record in current_records
        if (code := _text(record.get("ts_code"))) and code in stocks
    }
    if len(current) < 3:
        raise ValueError("the selected trade date has fewer than three stock rows")
    histories = _histories(daily_records_by_date)
    valid_market = [
        record
        for code, record in current.items()
        if _is_a_share_code(code) and _valid_daily(record)
    ]
    market = _market_summary(valid_market)
    market_segments = _market_segments(current)
    market_median = market.median_change_pct

    securities: dict[str, Security] = {}
    memberships: list[SectorMembership] = []
    features: list[RollingFeatures] = []
    sector_counts: Counter[str] = Counter()
    for code, record in current.items():
        stock = stocks[code]
        industry = _text(stock.get("industry"))
        if not industry or not _is_a_share_code(code) or not _valid_daily(record):
            continue
        sector_counts[industry] += 1
    for code, record in current.items():
        stock = stocks[code]
        industry = _text(stock.get("industry"))
        if (
            not industry
            or sector_counts[industry] < 3
            or not _is_a_share_code(code)
            or not _valid_daily(record)
        ):
            continue
        security = Security(
            code=code,
            name=_text(stock.get("name")) or code,
            market=code.rpartition(".")[2],
        )
        securities[code] = security
        memberships.append(
            SectorMembership(
                security=security,
                sector_code=industry,
                sector_name=industry,
                sector_type="industry",
                member_count=sector_counts[industry],
                effective_date=trade_date,
                source_ts=observed_at,
                received_ts=generated_at,
                provider_version="tushare-proxy-daily",
                config_version="post-close-review-v1",
                quality=DataQuality.DEGRADED,
                source_timestamp_kind=SourceTimestampKind.RECEIVED_FALLBACK,
            )
        )
        change = _change_pct(record)
        history = histories.get(code, ())
        prior_highs: list[float] = []
        for row in history:
            row_date = _record_date(row)
            if row_date is not None and row_date < trade_date:
                prior_highs.append(_number(row.get("high")))
        prior_high = max(
            prior_highs,
            default=0.0,
        )
        features.append(
            RollingFeatures(
                code=code,
                source_ts=observed_at,
                change_pct=change,
                velocity_1m_pct=None,
                velocity_3m_pct=None,
                velocity_5m_pct=None,
                acceleration_pct=None,
                volume_delta_1m=None,
                amount_delta_1m=None,
                volume_ratio_1m=None,
                amount_ratio_1m=None,
                intraday_high_break=False,
                high_3d_break=_number(record.get("high")) > prior_high > 0,
                market_relative_strength=change - market_median,
            )
        )

    sector_engine = SectorEngine()
    metrics = sector_engine.calculate(tuple(features), tuple(memberships))
    fund_background = _fund_background(moneyflow_records)
    evaluated: list[PostCloseCandidate] = []
    for feature in features:
        code = feature.code
        record = current[code]
        stock = stocks[code]
        selection = sector_engine.select_for_security(
            code,
            tuple(memberships),
            metrics,
        )
        if selection is None:
            continue
        exclusion = _exclusion_reason(
            code,
            stock,
            record,
            open_dates,
            trade_date,
            mechanical_jump_codes,
        )
        if exclusion is not None:
            continue
        history = histories.get(code, ())
        amount_ratio = _amount_ratio(history, trade_date)
        trend_pct, highs_rising, lows_rising, amount_rising = _trend(
            history,
            trade_date,
        )
        relative = feature.change_pct - selection.metrics.median_change_pct
        close_score = _close_evidence_score(
            record,
            change_pct=feature.change_pct,
            amount_ratio=amount_ratio,
            relative_strength=relative,
            high_break=feature.high_3d_break,
        )
        trend_score = _trend_score(
            trend_pct,
            highs_rising=highs_rising,
            lows_rising=lows_rising,
            amount_rising=amount_rising,
        )
        core_score = min(70.0, selection.metrics.score + close_score + trend_score)
        review_formal = selection.gate_passed and feature.change_pct > 0
        level = "近" if not review_formal else "强" if core_score >= 50 else "中"
        reasons = _reasons(
            change_pct=feature.change_pct,
            sector=selection.metrics.sector_name,
            sector_up_ratio=selection.metrics.up_ratio,
            sector_strong_count=selection.metrics.strong_count,
            relative=relative,
            amount_ratio=amount_ratio,
            trend_pct=trend_pct,
            high_break=feature.high_3d_break,
            fund_background=fund_background.get(code, "日级资金未确认"),
        )
        evaluated.append(
            PostCloseCandidate(
                rank=0,
                code=code,
                name=securities[code].name,
                market=_market_name(code),
                close=_number(record.get("close")),
                change_pct=feature.change_pct,
                sector=selection.metrics.sector_name,
                sector_code=selection.metrics.sector_code,
                level=level,
                core_score=round(core_score, 4),
                sector_score=selection.metrics.score,
                close_evidence_score=round(close_score, 4),
                trend_score=round(trend_score, 4),
                sector_up_ratio=selection.metrics.up_ratio,
                sector_strong_count=selection.metrics.strong_count,
                sector_relative_strength=round(relative, 4),
                amount_ratio_3d=(
                    round(amount_ratio, 4) if amount_ratio is not None else None
                ),
                trend_3d_pct=round(trend_pct, 4),
                daily_fund_background=fund_background.get(
                    code,
                    "日级资金未确认",
                ),
                reasons=reasons,
            )
        )
    evaluated.sort(
        key=lambda item: (
            item.level == "近",
            -item.core_score,
            -item.change_pct,
            item.code,
        )
    )
    selected = _diversified_top3(evaluated)
    if len(selected) != 3:
        raise ValueError("post-close review could not form three observation rows")
    top3 = tuple(
        PostCloseCandidate(**{**asdict(candidate), "rank": rank})
        for rank, candidate in enumerate(selected, start=1)
    )
    top_sectors = tuple(
        PostCloseSector(
            name=item.sector_name,
            member_count=item.valid_count,
            up_ratio=item.up_ratio,
            strong_count=item.strong_count,
            median_change_pct=item.median_change_pct,
            score=item.score,
        )
        for item in sorted(
            (item for item in metrics.values() if item.gate_passed),
            key=lambda item: (-item.score, item.sector_name),
        )[:10]
    )
    fund_capability = "daily_only" if moneyflow_records else "unavailable"
    fund_summary = (
        "资金接口有当日日级记录，仅作收盘背景，未参与盘中增强或本次排序。"
        if moneyflow_records
        else "资金未确认，本次排序未使用资金项。"
    )
    limitations = [
        "这是盘后历史数据回溯，不是09:45、14:45或强异动的实时提醒证据。",
        "当日分钟历史接口未形成可用记录；1/3/5分钟涨速与突然加速均未填充。",
        "行业宽度使用Tushare股票基础资料的行业归属；概念板块未纳入本次回溯。",
        "日级moneyflow只作为收盘背景，资金评分保持0分。",
    ]
    if len(daily_records_by_date) < 4:
        limitations.append("前三个交易日背景读取不完整，三日趋势与成交额基线保持未确认。")
    return PostCloseReview(
        trade_date=trade_date.isoformat(),
        generated_at=generated_at.isoformat(),
        title=f"{trade_date.isoformat()} A股盘后回顾测试",
        verdict="RETROSPECTIVE_ONLY",
        market=market,
        market_segments=market_segments,
        top_sectors=top_sectors,
        top3=top3,
        fund_capability=fund_capability,
        fund_summary=fund_summary,
        data_limitations=tuple(limitations),
    )


def _stock_index(records: tuple[Record, ...]) -> dict[str, Record]:
    return {
        code: record
        for record in records
        if (code := _text(record.get("ts_code")))
    }


def _histories(
    records_by_date: dict[date, tuple[Record, ...]],
) -> dict[str, tuple[Record, ...]]:
    grouped: dict[str, list[Record]] = {}
    for rows in records_by_date.values():
        for row in rows:
            code = _text(row.get("ts_code"))
            if code:
                grouped.setdefault(code, []).append(row)
    return {
        code: tuple(sorted(rows, key=lambda row: _text(row.get("trade_date"))))
        for code, rows in grouped.items()
    }


def _market_summary(records: list[Record]) -> PostCloseMarket:
    changes = [_change_pct(record) for record in records]
    advances = sum(change > 0 for change in changes)
    declines = sum(change < 0 for change in changes)
    unchanged = len(changes) - advances - declines
    return PostCloseMarket(
        securities=len(records),
        advances=advances,
        declines=declines,
        unchanged=unchanged,
        up_ratio=round(advances / len(records), 6) if records else 0.0,
        median_change_pct=round(median(changes), 4) if changes else 0.0,
        strong_count=sum(change >= 2 for change in changes),
        amount_cny=round(
            sum(_number(record.get("amount")) * 1000 for record in records),
            2,
        ),
    )


def _market_segments(
    current: dict[str, Record],
) -> tuple[dict[str, str | int | float], ...]:
    grouped: dict[str, list[Record]] = {}
    for code, record in current.items():
        if _is_a_share_code(code) and _valid_daily(record):
            grouped.setdefault(_market_name(code), []).append(record)
    output: list[dict[str, str | int | float]] = []
    for name in ("沪市主板", "深市主板", "创业板", "科创板", "北交所"):
        rows = grouped.get(name, [])
        summary = _market_summary(rows)
        output.append(
            {
                "market": name,
                "securities": summary.securities,
                "advances": summary.advances,
                "declines": summary.declines,
                "up_ratio": summary.up_ratio,
                "median_change_pct": summary.median_change_pct,
                "strong_count": summary.strong_count,
                "amount_cny": summary.amount_cny,
            }
        )
    return tuple(output)


def _exclusion_reason(
    code: str,
    stock: Record,
    daily: Record,
    open_dates: tuple[date, ...],
    trade_date: date,
    mechanical_jump_codes: frozenset[str],
) -> str | None:
    if code.endswith(".BJ"):
        return "北交所不进入默认候选"
    name = (_text(stock.get("name")) or "").upper()
    if name.startswith(("ST", "*ST")):
        return "ST"
    if "退" in name:
        return "退市整理"
    if _number(daily.get("vol")) <= 0 or _number(daily.get("close")) <= 0:
        return "停牌"
    if code in mechanical_jump_codes:
        return "除权机械跳变"
    list_date = _compact_date(stock.get("list_date"))
    if list_date is None:
        return "上市日期不完整"
    if list_date >= min(open_dates, default=trade_date):
        listed = sum(list_date <= day <= trade_date for day in open_dates)
        if listed < 3:
            return "上市不足3个交易日"
    if _one_price_limit(code, daily):
        return "一字涨停无法参与"
    return None


def _close_evidence_score(
    record: Record,
    *,
    change_pct: float,
    amount_ratio: float | None,
    relative_strength: float,
    high_break: bool,
) -> float:
    low = _number(record.get("low"))
    high = _number(record.get("high"))
    close = _number(record.get("close"))
    position = (close - low) / (high - low) if high > low else 0.5
    score = min(10.0, max(0.0, change_pct) / 7.0 * 10.0)
    score += min(5.0, max(0.0, position) * 5.0)
    if amount_ratio is not None and amount_ratio > 1:
        score += min(6.0, (amount_ratio - 1.0) * 3.0)
    score += 4.0 if high_break else 0.0
    score += min(5.0, max(0.0, relative_strength))
    return min(30.0, score)


def _trend_score(
    trend_pct: float,
    *,
    highs_rising: bool,
    lows_rising: bool,
    amount_rising: bool,
) -> float:
    score = min(6.0, max(0.0, trend_pct) * 1.5)
    score += 1.5 if highs_rising else 0.0
    score += 1.5 if lows_rising else 0.0
    score += 1.0 if amount_rising else 0.0
    return min(10.0, score)


def _trend(
    history: tuple[Record, ...],
    trade_date: date,
) -> tuple[float, bool, bool, bool]:
    recent = [
        row
        for row in history
        if (row_date := _record_date(row)) is not None and row_date <= trade_date
    ][-3:]
    if len(recent) < 3:
        return 0.0, False, False, False
    baseline = _number(recent[0].get("pre_close"))
    close = _number(recent[-1].get("close"))
    trend_pct = (close / baseline - 1) * 100 if baseline > 0 and close > 0 else 0.0
    highs = [_number(row.get("high")) for row in recent]
    lows = [_number(row.get("low")) for row in recent]
    amounts = [_number(row.get("amount")) for row in recent]
    return (
        trend_pct,
        _strictly_rising(highs),
        _strictly_rising(lows),
        _strictly_rising(amounts),
    )


def _amount_ratio(
    history: tuple[Record, ...],
    trade_date: date,
) -> float | None:
    current = next(
        (
            row
            for row in history
            if _record_date(row) == trade_date
        ),
        None,
    )
    priors = [
        _number(row.get("amount"))
        for row in history
        if (row_date := _record_date(row)) is not None
        and row_date < trade_date
        and _number(row.get("amount")) > 0
    ][-3:]
    if current is None or not priors:
        return None
    baseline = mean(priors)
    return _number(current.get("amount")) / baseline if baseline > 0 else None


def _fund_background(records: tuple[Record, ...]) -> dict[str, str]:
    output: dict[str, str] = {}
    for row in records:
        code = _text(row.get("ts_code"))
        if not code:
            continue
        net = (
            _number(row.get("buy_elg_amount"))
            - _number(row.get("sell_elg_amount"))
            + _number(row.get("buy_lg_amount"))
            - _number(row.get("sell_lg_amount"))
        )
        output[code] = (
            "日级大单净流入背景"
            if net > 0
            else "日级大单净流出背景"
            if net < 0
            else "日级资金一般"
        )
    return output


def _reasons(
    *,
    change_pct: float,
    sector: str,
    sector_up_ratio: float,
    sector_strong_count: int,
    relative: float,
    amount_ratio: float | None,
    trend_pct: float,
    high_break: bool,
    fund_background: str,
) -> tuple[str, ...]:
    reasons = [
        f"收盘涨幅 {change_pct:+.2f}%",
        (
            f"{sector}行业上涨比例 {sector_up_ratio:.1%}，"
            f"{sector_strong_count}只涨幅达到2%"
        ),
        f"相对所属行业强 {relative:+.2f} 个百分点",
    ]
    if amount_ratio is not None:
        reasons.append(f"成交额为前三日均值的 {amount_ratio:.2f} 倍")
    if high_break:
        reasons.append("盘中最高价高于前三个交易日最高价")
    if trend_pct:
        reasons.append(f"最近三个交易日累计 {trend_pct:+.2f}%")
    reasons.append(fund_background)
    return tuple(reasons[:6])


def _diversified_top3(
    candidates: list[PostCloseCandidate],
) -> tuple[PostCloseCandidate, ...]:
    selected: list[PostCloseCandidate] = []
    sector_counts: Counter[str] = Counter()
    for candidate in candidates:
        if sector_counts[candidate.sector_code] >= 2:
            continue
        selected.append(candidate)
        sector_counts[candidate.sector_code] += 1
        if len(selected) == 3:
            return tuple(selected)
    for candidate in candidates:
        if candidate not in selected:
            selected.append(candidate)
            if len(selected) == 3:
                break
    return tuple(selected)


def _one_price_limit(code: str, record: Record) -> bool:
    prices = [
        _number(record.get(field))
        for field in ("open", "high", "low", "close")
    ]
    if min(prices) <= 0 or max(prices) - min(prices) > 1e-8:
        return False
    limit = 30.0 if code.endswith(".BJ") else 20.0 if code.startswith(
        ("300", "301", "688", "689")
    ) else 10.0
    return _change_pct(record) >= limit - 0.25


def _market_name(code: str) -> str:
    digits = code.split(".", maxsplit=1)[0]
    if code.endswith(".BJ"):
        return "北交所"
    if digits.startswith(("300", "301")):
        return "创业板"
    if digits.startswith(("688", "689")):
        return "科创板"
    return "沪市主板" if code.endswith(".SH") else "深市主板"


def _is_a_share_code(code: str) -> bool:
    digits = code.split(".", maxsplit=1)[0]
    if code.endswith(".BJ"):
        return len(digits) == 6 and digits.isdigit()
    if code.endswith(".SH"):
        return digits.startswith(("600", "601", "603", "605", "688", "689"))
    if code.endswith(".SZ"):
        return digits.startswith(("000", "001", "002", "003", "300", "301"))
    return False


def _valid_daily(record: Record) -> bool:
    return (
        _number(record.get("close")) > 0
        and _number(record.get("pre_close")) > 0
        and _number(record.get("high")) >= _number(record.get("low")) >= 0
    )


def _change_pct(record: Record) -> float:
    value = record.get("pct_chg")
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        try:
            return float(value)
        except ValueError:
            pass
    close = _number(record.get("close"))
    previous = _number(record.get("pre_close"))
    return (close / previous - 1) * 100 if previous > 0 else 0.0


def _record_date(record: Record) -> date | None:
    return _compact_date(record.get("trade_date"))


def _compact_date(value: object) -> date | None:
    text = _text(value).replace("-", "")
    if len(text) != 8 or not text.isdigit():
        return None
    try:
        return datetime.strptime(text, "%Y%m%d").date()
    except ValueError:
        return None


def _strictly_rising(values: list[float]) -> bool:
    return len(values) >= 2 and all(
        left < right for left, right in zip(values, values[1:])
    )


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return 0.0
    try:
        return float(value)
    except ValueError:
        return 0.0


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""
