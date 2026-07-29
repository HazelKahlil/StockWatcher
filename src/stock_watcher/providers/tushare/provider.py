from __future__ import annotations

from dataclasses import dataclass

from .capability_router import Capability, CapabilityRouter
from .health import ProviderHealthGate
from .models import TransportResult
from .transport_protocol import TransportRequest


@dataclass(slots=True)
class TushareProvider:
    router: CapabilityRouter
    super_prefix: str = "/tushare/pro"
    health_gate: ProviderHealthGate | None = None

    name: str = "tushare-compatible-http"
    version: str = "v0.3.1"

    def _call(
        self,
        capability: Capability,
        endpoint: str,
        *,
        api_name: str,
        params: dict[str, str | int | float | bool] | None = None,
        fields: tuple[str, ...] = (),
        realtime: bool = False,
        method: str = "POST",
        allow_empty: bool = False,
    ) -> TransportResult:
        transport = self.router.select(capability)
        selected_endpoint = endpoint if transport.profile_name == "super" else "/"
        result = transport.execute(
            TransportRequest(
                endpoint=selected_endpoint,
                api_name=api_name,
                params=params or {},
                fields=fields,
                realtime=realtime,
                method=method,
                allow_empty=allow_empty,
            )
        )
        if self.health_gate is not None and realtime:
            self.health_gate.observe(result)
        return result

    def provider_health(self, endpoint: str = "/health") -> TransportResult:
        return self._call(
            Capability.HEALTH,
            endpoint,
            api_name="health",
            method="GET",
        )

    def stock_list(self, **params: str | int | float | bool) -> TransportResult:
        return self._call(
            Capability.STOCK_LIST,
            f"{self.super_prefix}/stock_basic",
            api_name="stock_basic",
            params=params,
        )

    def trading_dates(self, **params: str | int | float | bool) -> TransportResult:
        return self._call(
            Capability.TRADE_CALENDAR,
            f"{self.super_prefix}/trade_cal",
            api_name="trade_cal",
            params=params,
        )

    def daily_bars(self, **params: str | int | float | bool) -> TransportResult:
        return self._call(
            Capability.DAILY,
            f"{self.super_prefix}/daily",
            api_name="daily",
            params=params,
        )

    def realtime_market_snapshot(
        self, **params: str | int | float | bool
    ) -> TransportResult:
        return self._call(
            Capability.REALTIME_SNAPSHOT,
            f"{self.super_prefix}/rt_k",
            api_name="rt_k",
            params=params,
            realtime=True,
        )

    def realtime_minutes(self, **params: str | int | float | bool) -> TransportResult:
        return self._call(
            Capability.REALTIME_MINUTES,
            f"{self.super_prefix}/rt_min",
            api_name="rt_min",
            params=params,
            realtime=True,
        )

    def historical_minutes(self, **params: str | int | float | bool) -> TransportResult:
        return self._call(
            Capability.HISTORICAL_MINUTES,
            f"{self.super_prefix}/a_share_mins",
            api_name="a_share_mins",
            params=params,
        )

    def sector_membership(self, **params: str | int | float | bool) -> TransportResult:
        return self._call(
            Capability.SECTOR_CLASSIFY,
            f"{self.super_prefix}/index_classify",
            api_name="index_classify",
            params=params,
        )

    def sector_components(self, **params: str | int | float | bool) -> TransportResult:
        return self._call(
            Capability.SECTOR_COMPONENTS,
            f"{self.super_prefix}/index_member_all",
            api_name="index_member_all",
            params=params,
        )
