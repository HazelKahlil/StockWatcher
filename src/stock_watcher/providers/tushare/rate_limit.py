from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass


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
