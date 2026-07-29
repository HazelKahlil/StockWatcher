from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import timedelta
from statistics import median
from typing import cast

from stock_watcher.domain import RealtimeQuote, RollingFeatures


class SnapshotSequenceError(ValueError):
    """Raised when a supplier timestamp or cumulative field moves backwards."""


@dataclass(frozen=True, slots=True)
class FeatureConfig:
    retention_minutes: int = 15
    baseline_tolerance_seconds: int = 45
    ratio_baseline_windows: int = 5


class MarketSnapshotBuffer:
    """In-memory full-market ring buffer used for deterministic rolling features."""

    def __init__(self, config: FeatureConfig = FeatureConfig()) -> None:
        self.config = config
        self._quotes: dict[str, deque[RealtimeQuote]] = defaultdict(deque)

    def clear(self) -> None:
        self._quotes.clear()

    def prime(self, quotes: tuple[RealtimeQuote, ...]) -> None:
        """Preload historical minute observations without emitting features."""
        for quote in sorted(quotes, key=lambda item: (item.source_ts, item.security.code)):
            self._append(quote)

    def update(
        self,
        quotes: tuple[RealtimeQuote, ...],
        *,
        high_3d: dict[str, float] | None = None,
    ) -> tuple[RollingFeatures, ...]:
        if not quotes:
            return ()
        changes = [
            _percent(quote.price, quote.previous_close)
            for quote in quotes
            if quote.price > 0 and quote.previous_close > 0
        ]
        market_change = median(changes) if changes else 0.0
        output: list[RollingFeatures] = []
        for quote in sorted(quotes, key=lambda item: item.security.code):
            prior_high = max(
                (item.high for item in self._quotes.get(quote.security.code, ())),
                default=quote.high,
            )
            self._append(quote)
            points = self._quotes[quote.security.code]
            velocity_1m = self._velocity(points, quote, 1)
            velocity_3m = self._velocity(points, quote, 3)
            velocity_5m = self._velocity(points, quote, 5)
            volume_delta = self._cumulative_delta(points, quote, 1, "volume_shares")
            amount_delta = self._cumulative_delta(points, quote, 1, "amount_cny")
            volume_ratio = self._window_ratio(points, quote, "volume_shares")
            amount_ratio = self._window_ratio(points, quote, "amount_cny")
            change_pct = _percent(quote.price, quote.previous_close)
            output.append(
                RollingFeatures(
                    code=quote.security.code,
                    source_ts=quote.source_ts,
                    change_pct=change_pct,
                    velocity_1m_pct=velocity_1m,
                    velocity_3m_pct=velocity_3m,
                    velocity_5m_pct=velocity_5m,
                    acceleration_pct=(
                        velocity_1m - velocity_3m / 3
                        if velocity_1m is not None and velocity_3m is not None
                        else None
                    ),
                    volume_delta_1m=volume_delta,
                    amount_delta_1m=amount_delta,
                    volume_ratio_1m=volume_ratio,
                    amount_ratio_1m=amount_ratio,
                    intraday_high_break=quote.price > prior_high and prior_high > 0,
                    high_3d_break=(
                        quote.price > high_3d[quote.security.code]
                        if high_3d and quote.security.code in high_3d
                        else False
                    ),
                    market_relative_strength=change_pct - market_change,
                )
            )
        return tuple(output)

    def _append(self, quote: RealtimeQuote) -> None:
        points = self._quotes[quote.security.code]
        if points:
            previous = points[-1]
            if quote.source_ts < previous.source_ts:
                raise SnapshotSequenceError(
                    f"source timestamp moved backwards for {quote.security.code}"
                )
            if quote.source_ts == previous.source_ts:
                if quote == previous:
                    return
                raise SnapshotSequenceError(
                    f"conflicting duplicate timestamp for {quote.security.code}"
                )
            if (
                quote.volume_shares < previous.volume_shares
                or quote.amount_cny < previous.amount_cny
            ):
                raise SnapshotSequenceError(
                    f"cumulative volume or amount moved backwards for {quote.security.code}"
                )
        points.append(quote)
        cutoff = quote.source_ts - timedelta(minutes=self.config.retention_minutes)
        while points and points[0].source_ts < cutoff:
            points.popleft()

    def _baseline(
        self,
        points: deque[RealtimeQuote],
        quote: RealtimeQuote,
        minutes: int,
    ) -> RealtimeQuote | None:
        target = quote.source_ts - timedelta(minutes=minutes)
        eligible = [point for point in points if point.source_ts <= target]
        if not eligible:
            return None
        baseline = eligible[-1]
        if (target - baseline.source_ts).total_seconds() > self.config.baseline_tolerance_seconds:
            return None
        return baseline

    def _velocity(
        self,
        points: deque[RealtimeQuote],
        quote: RealtimeQuote,
        minutes: int,
    ) -> float | None:
        baseline = self._baseline(points, quote, minutes)
        if baseline is None or baseline.price <= 0:
            return None
        return _percent(quote.price, baseline.price)

    def _cumulative_delta(
        self,
        points: deque[RealtimeQuote],
        quote: RealtimeQuote,
        minutes: int,
        field: str,
    ) -> float | None:
        baseline = self._baseline(points, quote, minutes)
        if baseline is None:
            return None
        return cast(float, getattr(quote, field) - getattr(baseline, field))

    def _window_ratio(
        self,
        points: deque[RealtimeQuote],
        quote: RealtimeQuote,
        field: str,
    ) -> float | None:
        current = self._cumulative_delta(points, quote, 1, field)
        if current is None:
            return None
        deltas: list[float] = []
        for window in range(2, self.config.ratio_baseline_windows + 2):
            newer = self._baseline(points, quote, window - 1)
            older = self._baseline(points, quote, window)
            if newer is None or older is None:
                continue
            delta = getattr(newer, field) - getattr(older, field)
            if delta > 0:
                deltas.append(delta)
        if not deltas:
            return None
        baseline = median(deltas)
        return current / baseline if baseline > 0 else None


def _percent(current: float, baseline: float) -> float:
    return (current / baseline - 1.0) * 100.0 if baseline > 0 else 0.0
