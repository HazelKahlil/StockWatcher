from __future__ import annotations

from dataclasses import dataclass

from stock_watcher.domain import (
    CandidateInput,
    FundStatus,
    RealtimeQuote,
    RollingFeatures,
    SectorMembership,
    Security,
)

from .fund_engine import FundEngine
from .sector_engine import SectorEngine


@dataclass(frozen=True, slots=True)
class SecurityProfile:
    security: Security
    listed_trading_days: int
    is_st: bool = False
    is_delisting: bool = False
    is_corporate_action_day: bool = False


@dataclass(frozen=True, slots=True)
class ThreeDayTrend:
    cumulative_change_pct: float = 0.0
    highs_rising: bool = False
    lows_rising: bool = False
    amount_rising: bool = False
    highest_price: float | None = None


class CandidatePipeline:
    """Joins normalized realtime, rolling, sector, trend and optional fund inputs."""

    def __init__(self, sector_engine: SectorEngine | None = None) -> None:
        self.sector_engine = sector_engine or SectorEngine()

    def reset(self) -> None:
        self.sector_engine.reset()

    def build(
        self,
        quotes: tuple[RealtimeQuote, ...],
        features: tuple[RollingFeatures, ...],
        profiles: tuple[SecurityProfile, ...],
        memberships: tuple[SectorMembership, ...],
        *,
        trends: dict[str, ThreeDayTrend] | None = None,
        funds: dict[str, FundStatus] | None = None,
        config_version: str = "v1-real-candidates",
    ) -> tuple[CandidateInput, ...]:
        by_quote = {quote.security.code: quote for quote in quotes}
        by_feature = {feature.code: feature for feature in features}
        by_profile = {profile.security.code: profile for profile in profiles}
        sector_metrics = self.sector_engine.calculate(features, memberships)
        trends = trends or {}
        funds = funds or {}
        output: list[CandidateInput] = []
        for code in sorted(by_quote):
            quote = by_quote[code]
            feature = by_feature.get(code)
            profile = by_profile.get(code)
            if feature is None or profile is None:
                continue
            selection = self.sector_engine.select_for_security(
                code,
                memberships,
                sector_metrics,
            )
            trend = trends.get(code, ThreeDayTrend())
            fund = funds.get(code, FundEngine.unconfirmed())
            sector_name = selection.metrics.sector_name if selection else "板块待确认"
            sector_score = selection.metrics.score if selection else 0.0
            completeness = _completeness(feature, selection is not None)
            core_snapshot_complete = _core_snapshot_complete(
                quote,
                sector_present=selection is not None,
                trend_present=code in trends,
            )
            output.append(
                CandidateInput(
                    security=profile.security,
                    price=quote.price,
                    change_pct=feature.change_pct,
                    velocity_pct=feature.velocity_1m_pct or 0.0,
                    sector=sector_name,
                    sector_strength=sector_score,
                    trend_3d_pct=trend.cumulative_change_pct,
                    source_ts=quote.source_ts,
                    received_ts=quote.received_ts,
                    provider_version=quote.provider_version,
                    config_version=config_version,
                    is_st=profile.is_st,
                    is_delisting=profile.is_delisting,
                    is_suspended=quote.trading_state != "trading" or quote.price <= 0,
                    is_limit_up=_is_one_price_limit_up(quote),
                    is_new_or_corporate_action=(
                        profile.listed_trading_days < 3
                        or profile.is_corporate_action_day
                    ),
                    # A first full-market snapshot has no honest 1/3/5-minute
                    # baseline yet.  It may still produce explicitly weak
                    # ``近`` supplements when current quote, sector and
                    # completed-day trend are all present.  The unchanged
                    # completeness value prevents that cold-start result from
                    # being labelled ``强``; rolling windows promote later
                    # scans naturally without fabricating velocity.
                    is_complete=core_snapshot_complete or completeness >= 0.65,
                    velocity_1m_pct=feature.velocity_1m_pct,
                    velocity_3m_pct=feature.velocity_3m_pct,
                    velocity_5m_pct=feature.velocity_5m_pct,
                    acceleration_pct=feature.acceleration_pct,
                    volume_ratio_1m=feature.volume_ratio_1m,
                    amount_ratio_1m=feature.amount_ratio_1m,
                    intraday_high_break=feature.intraday_high_break,
                    high_3d_break=feature.high_3d_break,
                    market_relative_strength=feature.market_relative_strength,
                    sector_code=selection.metrics.sector_code if selection else "",
                    sector_type=selection.metrics.sector_type if selection else "unknown",
                    sector_gate_passed=selection.gate_passed if selection else False,
                    sector_up_ratio=selection.metrics.up_ratio if selection else None,
                    sector_strong_count=selection.metrics.strong_count if selection else None,
                    sector_rank_percentile=(
                        selection.rank_percentile if selection else None
                    ),
                    sector_relative_strength=(
                        feature.change_pct - selection.metrics.median_change_pct
                        if selection
                        else None
                    ),
                    sector_median_change_pct=(
                        selection.metrics.median_change_pct if selection else None
                    ),
                    sector_rank=selection.rank if selection else None,
                    sector_valid_count=(
                        selection.metrics.valid_count if selection else None
                    ),
                    highs_rising_3d=trend.highs_rising,
                    lows_rising_3d=trend.lows_rising,
                    amount_rising_3d=trend.amount_rising,
                    fund_status=fund,
                    data_completeness=completeness,
                )
            )
        return tuple(output)


def _completeness(feature: RollingFeatures, sector_present: bool) -> float:
    fields = (
        feature.velocity_1m_pct,
        feature.velocity_3m_pct,
        feature.velocity_5m_pct,
        feature.amount_ratio_1m,
        feature.market_relative_strength,
    )
    present = sum(value is not None for value in fields) + int(sector_present)
    return round(present / 6.0, 4)


def _core_snapshot_complete(
    quote: RealtimeQuote,
    *,
    sector_present: bool,
    trend_present: bool,
) -> bool:
    """Admit a fresh cross-sectional snapshot only as a low-confidence input."""

    return (
        quote.price > 0
        and quote.previous_close > 0
        and quote.volume_shares >= 0
        and quote.amount_cny >= 0
        and sector_present
        and trend_present
    )


def _is_one_price_limit_up(quote: RealtimeQuote) -> bool:
    if quote.previous_close <= 0 or quote.volume_shares <= 0:
        return False
    code = quote.security.code.split(".", maxsplit=1)[0]
    limit = 20.0 if code.startswith(("300", "301", "688", "689")) else 10.0
    change = (quote.price / quote.previous_close - 1.0) * 100.0
    return (
        change >= limit - 0.25
        and abs(quote.high - quote.low) < 1e-9
        and abs(quote.price - quote.high) < 1e-9
    )
