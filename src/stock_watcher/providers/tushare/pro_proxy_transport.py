from __future__ import annotations

import threading
import time
from collections.abc import Callable

from .fast_transport import FastTransport
from .models import TransportResult
from .transport_protocol import TransportRequest


class ProProxyTransport(FastTransport):
    """Tushare 15000 root-POST transport.

    The supplier's Pro proxy uses the standard Tushare JSON contract at the
    server root.  This named type keeps the ordinary product route separate
    from the legacy Fast/Super diagnostic terminology without duplicating the
    already tested request and response handling.
    """

    minimum_retry_interval_seconds = 0.5

    def __init__(
        self,
        *args: object,
        min_interval_seconds: float = 0.5,
        interval_clock: Callable[[], float] = time.monotonic,
        interval_sleeper: Callable[[float], None] = time.sleep,
        **kwargs: object,
    ) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        if min_interval_seconds < 0.5:
            raise ValueError("Pro request starts must be at least 0.5 seconds apart")
        self._minimum_interval = min_interval_seconds
        self._interval_clock = interval_clock
        self._interval_sleeper = interval_sleeper
        self._interval_lock = threading.Lock()
        self._last_started: float | None = None

    def execute(self, request: TransportRequest) -> TransportResult:
        with self._interval_lock:
            now = self._interval_clock()
            if self._last_started is not None:
                delay = self._minimum_interval - (now - self._last_started)
                if delay > 0:
                    self._interval_sleeper(delay)
                    now = self._interval_clock()
            self._last_started = now
            return super().execute(request)
