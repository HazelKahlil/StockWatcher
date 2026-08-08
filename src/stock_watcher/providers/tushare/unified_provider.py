from __future__ import annotations

from dataclasses import dataclass

from .models import TransportResult
from .native_realtime_transport import NativeRealtimeTransport
from .transport_protocol import TransportRequest, TushareTransport


@dataclass(slots=True)
class Tushare15000Provider:
    """One-token product provider for Pro data plus native realtime quotes."""

    pro: TushareTransport
    realtime: NativeRealtimeTransport

    name: str = "tushare_15000"
    version: str = "v1"

    def _pro_call(
        self,
        api_name: str,
        *,
        params: dict[str, str | int | float | bool] | None = None,
        fields: tuple[str, ...] = (),
        allow_empty: bool = False,
    ) -> TransportResult:
        return self.pro.execute(
            TransportRequest(
                endpoint="/",
                api_name=api_name,
                params=params or {},
                fields=fields,
                realtime=False,
                method="POST",
                allow_empty=allow_empty,
            )
        )

    def stock_list(self, **params: str | int | float | bool) -> TransportResult:
        return self._pro_call(
            "stock_basic",
            params=params,
            fields=(
                "ts_code",
                "symbol",
                "name",
                "area",
                "industry",
                "market",
                "list_date",
                "list_status",
            ),
        )

    def trading_dates(self, **params: str | int | float | bool) -> TransportResult:
        return self._pro_call(
            "trade_cal",
            params=params,
            fields=("exchange", "cal_date", "is_open", "pretrade_date"),
        )

    def daily_bars(self, **params: str | int | float | bool) -> TransportResult:
        return self._pro_call(
            "daily",
            params=params,
            fields=(
                "ts_code",
                "trade_date",
                "open",
                "high",
                "low",
                "close",
                "pre_close",
                "pct_chg",
                "vol",
                "amount",
            ),
        )

    def historical_minutes(self, **params: str | int | float | bool) -> TransportResult:
        return self._pro_call("stk_mins", params=params)

    def sector_classification(
        self,
        **params: str | int | float | bool,
    ) -> TransportResult:
        return self._pro_call("index_classify", params=params)

    def sector_components(
        self,
        **params: str | int | float | bool,
    ) -> TransportResult:
        return self._pro_call("index_member_all", params=params)

    def concept_classification(
        self,
        **params: str | int | float | bool,
    ) -> TransportResult:
        return self._pro_call("tdx_index", params=params, allow_empty=True)

    def concept_components(
        self,
        **params: str | int | float | bool,
    ) -> TransportResult:
        return self._pro_call("tdx_member", params=params, allow_empty=True)

    def moneyflow(self, **params: str | int | float | bool) -> TransportResult:
        return self._pro_call("moneyflow", params=params, allow_empty=True)

    def adjustment_factors(
        self,
        **params: str | int | float | bool,
    ) -> TransportResult:
        return self._pro_call(
            "adj_factor",
            params=params,
            fields=("ts_code", "trade_date", "adj_factor"),
            allow_empty=True,
        )

    def suspension_events(
        self,
        **params: str | int | float | bool,
    ) -> TransportResult:
        return self._pro_call(
            "suspend_d",
            params=params,
            fields=("ts_code", "trade_date", "suspend_timing", "suspend_type"),
            allow_empty=True,
        )

    def realtime_quotes(self, codes: tuple[str, ...]) -> TransportResult:
        return self.realtime.execute(
            TransportRequest(
                endpoint="tushare.realtime_quote:sina",
                api_name="realtime_quote",
                params={"ts_code": ",".join(codes)},
                fields=(
                    "ts_code",
                    "name",
                    "open",
                    "pre_close",
                    "price",
                    "high",
                    "low",
                    "vol",
                    "amount",
                    "source_ts",
                    "received_ts",
                ),
                realtime=True,
                method="SDK",
            )
        )
