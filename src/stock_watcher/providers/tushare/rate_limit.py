from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock


class ApplicationRequestBudget:
    """Serialize starts across all Tushare routes in one application.

    Tushare's Pro proxy and the approved native realtime SDK route share a
    Token-level allowance.  Per-transport throttles are not enough: a startup
    capability probe, a scan and a historical warmup can otherwise each begin
    their own burst.  This gate is deliberately process-local and injected
    into every product-route transport.

    The lock is held while waiting so two callers cannot reserve the same
    start slot.  The HTTP/SDK request itself runs outside this lock; the budget
    governs request *starts*, while the scan coordinator remains responsible
    for preventing overlapping full-market scans.
    """

    default_interval_seconds = 1.0
    minimum_interval_seconds = 0.6
    default_rate_limit_cooldown_seconds = 60.0

    def __init__(
        self,
        min_interval_seconds: float = default_interval_seconds,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if min_interval_seconds < self.minimum_interval_seconds:
            raise ValueError(
                "application request starts must be at least 0.6 seconds apart"
            )
        self.min_interval_seconds = min_interval_seconds
        self._clock = clock
        self._sleeper = sleeper
        self._lock = Lock()
        self._last_started: float | None = None
        self._not_before_by_lane: dict[str, float] = {}

    def acquire(self, lane: str = "shared") -> float:
        """Reserve the next request start and return any applied wait."""
        if not lane:
            raise ValueError("request budget lane must not be empty")
        with self._lock:
            now = self._clock()
            interval_deadline = (
                self._last_started + self.min_interval_seconds
                if self._last_started is not None
                else now
            )
            deadline = max(
                self._not_before_by_lane.get("shared", 0.0),
                self._not_before_by_lane.get(lane, 0.0),
                interval_deadline,
            )
            delay = max(0.0, deadline - now)
            if delay:
                self._sleeper(delay)
            # Test clocks are sometimes deliberately passive.  Reserving the
            # calculated deadline still prevents a second caller from sharing
            # this slot even when its fake sleeper does not advance time.
            self._last_started = max(deadline, self._clock())
            return delay

    def pause_for(self, seconds: float | None, *, lane: str = "shared") -> float:
        """Apply a 429 cooldown without conflating independent provider routes."""
        if not lane:
            raise ValueError("request budget lane must not be empty")
        delay = (
            self.default_rate_limit_cooldown_seconds
            if seconds is None
            else seconds
        )
        if delay < 0:
            raise ValueError("rate-limit delay must not be negative")
        with self._lock:
            self._not_before_by_lane[lane] = max(
                self._not_before_by_lane.get(lane, 0.0),
                self._clock() + delay,
            )
        return delay

    def cooldown_remaining(self, *, lane: str | None = None) -> float:
        with self._lock:
            now = self._clock()
            if lane is None:
                deadlines = self._not_before_by_lane.values()
                return max((max(0.0, deadline - now) for deadline in deadlines), default=0.0)
            return max(
                0.0,
                self._not_before_by_lane.get("shared", 0.0) - now,
                self._not_before_by_lane.get(lane, 0.0) - now,
            )

    @property
    def next_start_not_before(self) -> float:
        with self._lock:
            interval_deadline = (
                self._last_started + self.min_interval_seconds
                if self._last_started is not None
                else 0.0
            )
            return max(
                (*self._not_before_by_lane.values(), interval_deadline),
                default=interval_deadline,
            )


@dataclass(slots=True)
class RateLimitGuard:
    clock: Callable[[], float] = time.monotonic
    sleeper: Callable[[float], None] = time.sleep
    _not_before: float = 0.0

    def wait(self) -> None:
        delay = self._not_before - self.clock()
        if delay > 0:
            self.sleeper(delay)

    def pause_for(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("rate-limit delay must not be negative")
        self._not_before = max(self._not_before, self.clock() + seconds)
