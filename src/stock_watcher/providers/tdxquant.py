from __future__ import annotations

import importlib
import json
import socket
from collections import deque
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from pathlib import Path
from types import ModuleType
from typing import Any, Protocol, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from stock_watcher.domain import (
    SHANGHAI,
    DataQuality,
    HealthState,
    HistoricalBar,
    MarketEvent,
    ProviderHealth,
    SectorMembership,
    Security,
    Snapshot,
    SourceTimestampKind,
    TradingDate,
)

from .availability import TDXQUANT_DESCRIPTOR, ProviderDescriptor, ProviderUnavailable

JsonObject = dict[str, Any]


class TdxFailureReason(StrEnum):
    DEPENDENCY_MISSING = "dependency_missing"
    TERMINAL_NOT_INSTALLED = "terminal_not_installed"
    TERMINAL_NOT_RUNNING = "terminal_not_running"
    NOT_LOGGED_IN = "not_logged_in"
    SERVICE_UNREACHABLE = "service_unreachable"
    METHOD_UNAVAILABLE = "method_unavailable"
    FIELD_UNAVAILABLE = "field_unavailable"
    TIMEOUT = "timeout"
    NON_TRADING_SESSION = "non_trading_session"
    DATA_STALE = "data_stale"
    DATA_INTERRUPTED = "data_interrupted"
    USER_PAUSED = "user_paused"
    INVALID_RESPONSE = "invalid_response"


FAILURE_MESSAGES_ZH: dict[TdxFailureReason, str] = {
    TdxFailureReason.DEPENDENCY_MISSING: (
        "未找到可选的 tqcenter Python 客户端，请改用本机 HTTP 模式或安装官方组件。"
    ),
    TdxFailureReason.TERMINAL_NOT_INSTALLED: (
        "未找到官方通达信金融终端，请先安装免费的 64 位“金融终端（量化模拟）”。"
    ),
    TdxFailureReason.TERMINAL_NOT_RUNNING: "通达信终端尚未启动，请先启动终端并保持运行。",
    TdxFailureReason.NOT_LOGGED_IN: "通达信终端尚未登录或行情权限未就绪，请在官方终端内完成登录。",
    TdxFailureReason.SERVICE_UNREACHABLE: (
        "TQ 本机服务不可达，请确认终端支持 TQ，且 127.0.0.1:17709 已启动。"
    ),
    TdxFailureReason.METHOD_UNAVAILABLE: (
        "当前终端未提供所需的官方 TdxQuant 接口，请检查终端版本与权限。"
    ),
    TdxFailureReason.FIELD_UNAVAILABLE: (
        "接口未返回所需字段；该能力保持未就绪，不会用替代字段冒充。"
    ),
    TdxFailureReason.TIMEOUT: "TQ 本机服务响应超时，请确认终端行情已连接后重试。",
    TdxFailureReason.NON_TRADING_SESSION: (
        "当前不在 A 股连续交易时段；可执行预检，但不会产生新候选。"
    ),
    TdxFailureReason.DATA_STALE: "行情已过期，系统已停止产生新候选。",
    TdxFailureReason.DATA_INTERRUPTED: "行情数据中断，系统已停止产生新候选。",
    TdxFailureReason.USER_PAUSED: "用户已暂停实时观察；恢复前不会产生新候选。",
    TdxFailureReason.INVALID_RESPONSE: "TdxQuant 返回了无法识别的数据，已安全停止候选输出。",
}


class TdxTransportError(RuntimeError):
    def __init__(self, reason: TdxFailureReason, detail: str = "") -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(FAILURE_MESSAGES_ZH[reason] + (f"（{detail}）" if detail else ""))


class TdxTransport(Protocol):
    name: str

    def call(self, method: str, params: Mapping[str, object]) -> object: ...


def _validate_loopback_endpoint(endpoint: str) -> None:
    parsed = urlparse(endpoint)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("TdxQuant HTTP endpoint must be an http loopback address")
    if parsed.port != 17709:
        raise ValueError("TdxQuant HTTP endpoint must use the documented local port 17709")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("TdxQuant HTTP endpoint must not contain a path, query, or fragment")


@dataclass(slots=True)
class TdxHttpTransport:
    """Official local TQ HTTP bridge documented by TdxQuant.

    The bridge is deliberately restricted to loopback. It is not a hosted HTTPS
    API and no credential is accepted or logged by this adapter.
    """

    endpoint: str = "http://127.0.0.1:17709/"
    timeout_seconds: float = 5.0
    name: str = "official-loopback-http"
    _request_id: int = 0

    def __post_init__(self) -> None:
        _validate_loopback_endpoint(self.endpoint)
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")

    def call(self, method: str, params: Mapping[str, object]) -> object:
        self._request_id += 1
        body = json.dumps(
            {"id": self._request_id, "method": method, "params": dict(params)},
            ensure_ascii=False,
        ).encode("utf-8")
        request = Request(
            self.endpoint,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:  # noqa: S310
                payload = json.loads(response.read().decode("utf-8"))
        except TimeoutError as error:
            raise TdxTransportError(TdxFailureReason.TIMEOUT) from error
        except HTTPError as error:
            raise TdxTransportError(
                TdxFailureReason.SERVICE_UNREACHABLE, f"HTTP {error.code}"
            ) from error
        except URLError as error:
            reason = (
                TdxFailureReason.TIMEOUT
                if isinstance(error.reason, (TimeoutError, socket.timeout))
                else TdxFailureReason.SERVICE_UNREACHABLE
            )
            raise TdxTransportError(reason) from error
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise TdxTransportError(TdxFailureReason.INVALID_RESPONSE) from error
        if not isinstance(payload, dict):
            raise TdxTransportError(TdxFailureReason.INVALID_RESPONSE)
        if "result" not in payload:
            raise TdxTransportError(TdxFailureReason.INVALID_RESPONSE, "missing result")
        return _unwrap_tdx_result(payload["result"])


@dataclass(slots=True)
class TdxPythonTransport:
    """Delayed loader for the official ``tqcenter`` module bundled on Windows."""

    initialize_from: Path
    module_name: str = "tqcenter"
    name: str = "official-python-client"
    _module: ModuleType | None = None
    _client: object | None = None

    def _load(self) -> object:
        if self._client is not None:
            return self._client
        try:
            self._module = importlib.import_module(self.module_name)
        except ImportError as error:
            raise TdxTransportError(TdxFailureReason.DEPENDENCY_MISSING) from error
        client = getattr(self._module, "tq", None)
        if client is None:
            raise TdxTransportError(TdxFailureReason.INVALID_RESPONSE, "tqcenter.tq missing")
        initialize = getattr(client, "initialize", None)
        if not callable(initialize):
            raise TdxTransportError(TdxFailureReason.INVALID_RESPONSE, "initialize missing")
        initialize(str(self.initialize_from.resolve()))
        self._client = client
        return client

    def call(self, method: str, params: Mapping[str, object]) -> object:
        client = self._load()
        function = getattr(client, method, None)
        if not callable(function):
            raise TdxTransportError(TdxFailureReason.METHOD_UNAVAILABLE, method)
        try:
            return _unwrap_tdx_result(function(**dict(params)))
        except TdxTransportError:
            raise
        except TimeoutError as error:
            raise TdxTransportError(TdxFailureReason.TIMEOUT, method) from error
        except Exception as error:
            message = str(error)
            lowered = message.lower()
            if "login" in lowered or "登录" in message:
                reason = TdxFailureReason.NOT_LOGGED_IN
            elif "connect" in lowered or "连接" in message:
                reason = TdxFailureReason.SERVICE_UNREACHABLE
            else:
                reason = TdxFailureReason.INVALID_RESPONSE
            raise TdxTransportError(reason, method) from error


def _unwrap_tdx_result(value: object) -> object:
    if not isinstance(value, dict):
        return value
    error_id = str(value.get("ErrorId", "0"))
    if error_id not in {"", "0", "None"}:
        detail = str(value.get("ErrorMsg") or value.get("ErrorInfo") or error_id)
        lowered = detail.lower()
        if "login" in lowered or "登录" in detail:
            reason = TdxFailureReason.NOT_LOGGED_IN
        elif "method" in lowered or "函数" in detail:
            reason = TdxFailureReason.METHOD_UNAVAILABLE
        else:
            reason = TdxFailureReason.INVALID_RESPONSE
        raise TdxTransportError(reason, detail)
    return value.get("Value", value)


@dataclass(frozen=True, slots=True)
class TdxQuantConfig:
    stock_codes: tuple[str, ...] = ()
    provider_version: str = "tdxquant-official-unverified"
    config_version: str = "v0.3"
    stale_after_seconds: float = 20.0
    min_recovery_samples: int = 3
    user_paused: bool = False

    def __post_init__(self) -> None:
        if self.stale_after_seconds <= 0:
            raise ValueError("stale_after_seconds must be positive")
        if self.min_recovery_samples < 1:
            raise ValueError("min_recovery_samples must be positive")


def _number(value: object, field: str, *, default: float | None = None) -> float:
    if value in (None, "") and default is not None:
        return default
    try:
        return float(cast(str | int | float, value))
    except (TypeError, ValueError) as error:
        raise TdxTransportError(TdxFailureReason.FIELD_UNAVAILABLE, field) from error


def _security(code: str, name: str = "") -> Security:
    normalized = code.upper()
    if normalized.endswith(".SH"):
        market = "SH"
    elif normalized.endswith(".SZ"):
        market = "SZ"
    elif normalized.endswith(".BJ"):
        market = "BJ"
    else:
        raise TdxTransportError(TdxFailureReason.INVALID_RESPONSE, f"unknown market: {code}")
    return Security(code=normalized, name=name or normalized, market=market)


def _parse_source_timestamp(
    payload: Mapping[str, object], received_ts: datetime
) -> tuple[datetime, SourceTimestampKind]:
    raw = payload.get("HqDateTime") or payload.get("DateTime")
    if raw:
        text = "".join(character for character in str(raw) if character.isdigit())
        for pattern, length in (("%Y%m%d%H%M%S", 14), ("%Y%m%d%H%M", 12)):
            if len(text) >= length:
                try:
                    return (
                        datetime.strptime(text[:length], pattern).replace(tzinfo=SHANGHAI),
                        SourceTimestampKind.PROVIDER,
                    )
                except ValueError:
                    pass
    date_text = str(payload.get("HqDate") or payload.get("Date") or "")
    time_text = str(payload.get("HqTime") or payload.get("Time") or "")
    digits = "".join(character for character in date_text + time_text if character.isdigit())
    if len(digits) >= 12:
        try:
            return (
                datetime.strptime(digits[:14].ljust(14, "0"), "%Y%m%d%H%M%S").replace(
                    tzinfo=SHANGHAI
                ),
                SourceTimestampKind.PROVIDER,
            )
        except ValueError:
            pass
    return received_ts, SourceTimestampKind.RECEIVED_FALLBACK


def is_continuous_trading_session(moment: datetime, trading_dates: set[date] | None = None) -> bool:
    local = moment.astimezone(SHANGHAI)
    if trading_dates is not None:
        if local.date() not in trading_dates:
            return False
    elif local.weekday() >= 5:
        return False
    clock = local.time().replace(tzinfo=None)
    return time(9, 30) <= clock <= time(11, 30) or time(13, 0) <= clock <= time(15, 0)


class TdxQuantProvider:
    """Official TdxQuant adapter with explicit M0-safe degradation.

    A configured transport can read documented public market endpoints. Candidate
    output remains blocked when TdxQuant does not provide a precise source timestamp,
    when data is stale, or while recovering from an interruption.
    """

    name = "tdxquant"
    version = "v0.3-pre-m0"
    descriptor: ProviderDescriptor = TDXQUANT_DESCRIPTOR

    def __init__(
        self,
        transport: TdxTransport | None = None,
        config: TdxQuantConfig | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.transport = transport
        self.config = config or TdxQuantConfig()
        self._clock = clock or (lambda: datetime.now(SHANGHAI))
        self._reconnecting = False
        self._recovery_cutoff: datetime | None = None
        self._recovery_samples = 0
        self._seen: set[tuple[str, datetime]] = set()
        self._seen_order: deque[tuple[str, datetime]] = deque()

    def _require_transport(self) -> TdxTransport:
        if self.transport is None:
            raise ProviderUnavailable(self.descriptor.detail)
        return self.transport

    def stock_list(self, market: str = "5") -> tuple[Security, ...]:
        raw = self._require_transport().call(
            "get_stock_list", {"market": market, "list_type": 0}
        )
        if isinstance(raw, dict):
            raw = raw.get("stock_list") or raw.get("Stocks") or list(raw)
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
            raise TdxTransportError(TdxFailureReason.INVALID_RESPONSE, "get_stock_list")
        return tuple(_security(str(code)) for code in raw)

    def price_volume(self, codes: Sequence[str]) -> dict[str, Snapshot]:
        received = self._clock()
        raw = self._require_transport().call("get_pricevol", {"stock_list": list(codes)})
        if not isinstance(raw, dict):
            raise TdxTransportError(TdxFailureReason.INVALID_RESPONSE, "get_pricevol")
        snapshots: dict[str, Snapshot] = {}
        for code, fields in raw.items():
            if not isinstance(fields, dict):
                continue
            source_ts, timestamp_kind = _parse_source_timestamp(fields, received)
            quality = (
                DataQuality.GOOD
                if timestamp_kind is SourceTimestampKind.PROVIDER
                else DataQuality.DEGRADED
            )
            snapshots[str(code)] = Snapshot(
                security=_security(str(code), str(fields.get("Name") or "")),
                price=_number(fields.get("Now"), "Now"),
                previous_close=_number(fields.get("LastClose"), "LastClose"),
                volume=_number(fields.get("Volume"), "Volume", default=0),
                amount=(
                    _number(fields.get("Amount"), "Amount")
                    if fields.get("Amount") not in (None, "")
                    else None
                ),
                source_ts=source_ts,
                received_ts=received,
                provider_version=self.config.provider_version,
                config_version=self.config.config_version,
                quality=quality,
                source_timestamp_kind=timestamp_kind,
            )
        return snapshots

    def market_snapshot(self, code: str) -> Snapshot:
        received = self._clock()
        raw = self._require_transport().call(
            "get_market_snapshot", {"stock_code": code, "field_list": []}
        )
        if not isinstance(raw, dict):
            raise TdxTransportError(TdxFailureReason.INVALID_RESPONSE, "get_market_snapshot")
        more = self._require_transport().call(
            "get_more_info",
            {"stock_code": code, "field_list": []},
        )
        metadata = more if isinstance(more, dict) else {}
        combined = {**raw, **metadata}
        source_ts, timestamp_kind = _parse_source_timestamp(combined, received)
        suspended = str(metadata.get("TPFlag", "0")) not in {"", "0"}
        quality = (
            DataQuality.GOOD
            if timestamp_kind is SourceTimestampKind.PROVIDER
            else DataQuality.DEGRADED
        )
        return Snapshot(
            security=_security(code, str(raw.get("Name") or "")),
            price=_number(raw.get("Now"), "Now"),
            previous_close=_number(raw.get("LastClose"), "LastClose"),
            volume=_number(raw.get("Volume"), "Volume", default=0),
            amount=_number(raw.get("Amount"), "Amount", default=0),
            trading_state="suspended" if suspended else "trading",
            source_ts=source_ts,
            received_ts=received,
            provider_version=self.config.provider_version,
            config_version=self.config.config_version,
            quality=quality,
            source_timestamp_kind=timestamp_kind,
        )

    def historical_bars(
        self, code: str, *, period: str = "1m", count: int = 720
    ) -> tuple[HistoricalBar, ...]:
        if period not in {"1m", "5m", "1d"}:
            raise ValueError("period must be one of 1m, 5m, or 1d")
        if not 1 <= count <= 24_000:
            raise ValueError("count must be between 1 and 24000")
        received = self._clock()
        raw = self._require_transport().call(
            "get_market_data",
            {
                "stock_list": [code],
                "count": count,
                "dividend_type": "none",
                "period": period,
            },
        )
        if isinstance(raw, dict) and code in raw and isinstance(raw[code], dict):
            raw = raw[code]
        if not isinstance(raw, dict):
            raise TdxTransportError(TdxFailureReason.INVALID_RESPONSE, "get_market_data")
        dates = list(cast(Sequence[object], raw.get("Date", [])))
        times = list(cast(Sequence[object], raw.get("Time", [])))
        fields = {
            name: list(cast(Sequence[object], raw.get(name, [])))
            for name in ("Open", "High", "Low", "Close", "Volume", "Amount")
        }
        if not dates or any(len(values) != len(dates) for values in fields.values()):
            raise TdxTransportError(TdxFailureReason.FIELD_UNAVAILABLE, "historical bars")
        bars: list[HistoricalBar] = []
        security = _security(code)
        for index, date_value in enumerate(dates):
            time_value = times[index] if index < len(times) else "0"
            digits = "".join(
                character for character in f"{date_value}{time_value}" if character.isdigit()
            )
            source_ts = datetime.strptime(digits[:14].ljust(14, "0"), "%Y%m%d%H%M%S").replace(
                tzinfo=SHANGHAI
            )
            bars.append(
                HistoricalBar(
                    security=security,
                    period=period,
                    source_ts=source_ts,
                    received_ts=received,
                    open=_number(fields["Open"][index], "Open"),
                    high=_number(fields["High"][index], "High"),
                    low=_number(fields["Low"][index], "Low"),
                    close=_number(fields["Close"][index], "Close"),
                    volume=_number(fields["Volume"][index], "Volume", default=0),
                    amount=_number(fields["Amount"][index], "Amount", default=0),
                    provider_version=self.config.provider_version,
                    config_version=self.config.config_version,
                )
            )
        return tuple(bars)

    def sectors(self, code: str) -> tuple[SectorMembership, ...]:
        received = self._clock()
        raw = self._require_transport().call("get_relation", {"stock_code": code})
        if isinstance(raw, dict):
            raw = raw.get("relations") or raw.get("Relations") or raw.get("Value")
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
            raise TdxTransportError(TdxFailureReason.INVALID_RESPONSE, "get_relation")
        memberships: list[SectorMembership] = []
        security = _security(code)
        for item in raw:
            if not isinstance(item, Mapping):
                raise TdxTransportError(TdxFailureReason.INVALID_RESPONSE, "get_relation item")
            required = ("BlockName", "BlockType", "GPNume")
            missing = next((field for field in required if item.get(field) in (None, "")), None)
            if missing is not None:
                raise TdxTransportError(TdxFailureReason.FIELD_UNAVAILABLE, missing)
            member_count = _number(item.get("GPNume"), "GPNume")
            if not member_count.is_integer():
                raise TdxTransportError(TdxFailureReason.INVALID_RESPONSE, "GPNume")
            memberships.append(
                SectorMembership(
                    security=security,
                    sector_code=str(item.get("BlockCode") or "0"),
                    sector_name=str(item["BlockName"]),
                    sector_type=str(item["BlockType"]),
                    member_count=int(member_count),
                    effective_date=received.date(),
                    source_ts=received,
                    received_ts=received,
                    provider_version=self.config.provider_version,
                    config_version=self.config.config_version,
                )
            )
        return tuple(memberships)

    def trading_dates(
        self, start: str = "", end: str = "", *, count: int = -1
    ) -> tuple[TradingDate, ...]:
        if count == 0 or count < -1:
            raise ValueError("count must be -1 or a positive integer")
        received = self._clock()
        raw = self._require_transport().call(
            "get_trading_dates",
            {"market": "SH", "start_time": start, "end_time": end, "count": count},
        )
        if isinstance(raw, dict):
            raw = raw.get("Dates") or raw.get("dates") or list(raw)
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
            raise TdxTransportError(TdxFailureReason.INVALID_RESPONSE, "get_trading_dates")
        dates: list[TradingDate] = []
        for value in raw:
            try:
                parsed = datetime.strptime(str(value), "%Y%m%d").date()
            except ValueError as error:
                raise TdxTransportError(
                    TdxFailureReason.FIELD_UNAVAILABLE, "trading_date"
                ) from error
            dates.append(
                TradingDate(
                    market="SH",
                    trading_date=parsed,
                    is_open=True,
                    source_ts=received,
                    received_ts=received,
                    provider_version=self.config.provider_version,
                    config_version=self.config.config_version,
                )
            )
        return tuple(dates)

    def events(self) -> Iterator[MarketEvent]:
        self._require_transport()
        if self.config.user_paused:
            now = self._clock()
            yield self._health_event(
                HealthState.STOPPED, now, now, FAILURE_MESSAGES_ZH[TdxFailureReason.USER_PAUSED]
            )
            return
        current = self._clock()
        if not is_continuous_trading_session(current):
            yield self._health_event(
                HealthState.STOPPED,
                current,
                current,
                FAILURE_MESSAGES_ZH[TdxFailureReason.NON_TRADING_SESSION],
            )
            return
        codes = self.config.stock_codes
        if not codes:
            raise ProviderUnavailable("TdxQuantProvider requires explicit stock_codes")
        for code in codes:
            try:
                snapshot = self.market_snapshot(code)
            except TdxTransportError as error:
                now = self._clock()
                self._reconnecting = True
                self._recovery_cutoff = now
                self._recovery_samples = 0
                yield self._health_event(HealthState.STOPPED, now, now, str(error))
                return
            key = (snapshot.security.code, snapshot.source_ts)
            if key in self._seen:
                continue
            self._seen.add(key)
            self._seen_order.append(key)
            if len(self._seen_order) > 50_000:
                self._seen.discard(self._seen_order.popleft())
            age = snapshot.received_ts - snapshot.source_ts
            if (
                self._reconnecting
                and self._recovery_cutoff is not None
                and snapshot.source_ts <= self._recovery_cutoff
            ):
                state = HealthState.STALE
                detail = "恢复样本不晚于中断截止时间；已拒绝旧数据。"
            elif age > timedelta(seconds=self.config.stale_after_seconds):
                self._enter_recovery(snapshot.received_ts)
                state = HealthState.STALE
                detail = FAILURE_MESSAGES_ZH[TdxFailureReason.DATA_STALE]
            elif snapshot.source_timestamp_kind is SourceTimestampKind.RECEIVED_FALLBACK:
                self._enter_recovery(snapshot.received_ts)
                state = HealthState.WARMING
                detail = "接口未提供精确 source_ts；已保留接收时间并阻止候选输出。"
            elif snapshot.trading_state == "suspended":
                self._enter_recovery(snapshot.received_ts)
                state = HealthState.STALE
                detail = "证券当前停牌；不会产生新候选。"
            elif self._reconnecting:
                self._recovery_samples += 1
                if self._recovery_samples <= self.config.min_recovery_samples:
                    state = HealthState.WARMING
                    detail = (
                        f"断线恢复预热 {self._recovery_samples}/"
                        f"{self.config.min_recovery_samples}；候选保持关闭。"
                    )
                else:
                    self._reconnecting = False
                    self._recovery_cutoff = None
                    self._recovery_samples = 0
                    state = HealthState.HEALTHY
                    detail = "官方 TdxQuant 行情已完成断线恢复预热。"
            else:
                state = HealthState.HEALTHY
                detail = "官方 TdxQuant 行情已归一化。"
            health = ProviderHealth(
                state=state,
                source_ts=snapshot.source_ts,
                received_ts=snapshot.received_ts,
                provider_version=self.config.provider_version,
                config_version=self.config.config_version,
                detail=detail,
            )
            yield MarketEvent(snapshot=snapshot, health=health)

    def _enter_recovery(self, cutoff: datetime) -> None:
        self._reconnecting = True
        self._recovery_cutoff = cutoff
        self._recovery_samples = 0

    def _health_event(
        self, state: HealthState, source_ts: datetime, received_ts: datetime, detail: str
    ) -> MarketEvent:
        return MarketEvent(
            snapshot=None,
            health=ProviderHealth(
                state=state,
                source_ts=source_ts,
                received_ts=received_ts,
                provider_version=self.config.provider_version,
                config_version=self.config.config_version,
                detail=detail,
            ),
        )
