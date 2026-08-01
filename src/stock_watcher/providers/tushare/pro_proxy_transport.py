from __future__ import annotations

import time
from collections.abc import Callable
from typing import TYPE_CHECKING

import requests

from stock_watcher.config import HttpProfile

from .fast_transport import FastTransport
from .rate_limit import ApplicationRequestBudget

if TYPE_CHECKING:
    from .http_transport import Clock, Sleeper


class ProProxyTransport(FastTransport):
    """Tushare 15000 root-POST transport.

    The supplier's Pro proxy uses the standard Tushare JSON contract at the
    server root.  This named type keeps the ordinary product route separate
    from the legacy Fast/Super diagnostic terminology without duplicating the
    already tested request and response handling.
    """

    def __init__(
        self,
        profile: HttpProfile,
        secret_getter: Callable[[], str | None],
        *,
        session: requests.Session | None = None,
        clock: Clock | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Sleeper = time.sleep,
        min_interval_seconds: float = 1.0,
        interval_clock: Callable[[], float] | None = None,
        interval_sleeper: Sleeper | None = None,
        request_budget: ApplicationRequestBudget | None = None,
    ) -> None:
        budget = request_budget or ApplicationRequestBudget(
            min_interval_seconds,
            clock=interval_clock or monotonic,
            sleeper=interval_sleeper or sleeper,
        )
        super().__init__(
            profile,
            secret_getter,
            session=session,
            clock=clock,
            monotonic=monotonic,
            sleeper=sleeper,
            request_budget=budget,
        )
