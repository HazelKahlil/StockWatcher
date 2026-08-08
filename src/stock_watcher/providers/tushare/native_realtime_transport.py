from __future__ import annotations

import importlib
import math
import re
import threading
import time
from collections.abc import Callable
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from io import StringIO
from typing import Protocol, cast
from zoneinfo import ZoneInfo

from stock_watcher.config import NativeRealtimeProfile

from .errors import ProviderError, ProviderFailureReason
from .models import (
    DataQuality,
    ProviderProvenance,
    Record,
    SourceTimestampKind,
    TransportResult,
)
from .rate_limit import ApplicationRequestBudget
from .transport_protocol import TransportRequest

Clock = Callable[[], datetime]
Monotonic = Callable[[], float]
SecretGetter = Callable[[], str | None]
Sleeper = Callable[[float], None]
ModuleImporter = Callable[[str], object]

SHANGHAI = ZoneInfo("Asia/Shanghai")
SDK_RUNTIME_LOCK = threading.RLock()
CODE_PATTERN = re.compile(r"^[0-9]{6}\.(?:SH|SZ|BJ)$")
NUMERIC_FIELDS = {
    "OPEN": "open",
    "PRE_CLOSE": "pre_close",
    "PRICE": "price",
    "HIGH": "high",
    "LOW": "low",
    "BID": "bid",
    "ASK": "ask",
    "VOLUME": "vol",
    "AMOUNT": "amount",
}


class NativeRealtimeClient(Protocol):
    version: str

    def configure(self, token: str, verify_url: str) -> None: ...

    def fetch(
        self,
        codes: tuple[str, ...],
        *,
        source: str,
    ) -> list[dict[str, object]] | None: ...


class RealtimeFrame(Protocol):
    def to_dict(self, *, orient: str) -> list[dict[str, object]]: ...


class TushareSdkModule(Protocol):
    __version__: str

    def realtime_quote(self, *, ts_code: str, src: str) -> RealtimeFrame | None: ...


class TushareConstantsModule(Protocol):
    verify_token_url: str


class TushareVerifyTokenModule(Protocol):
    get_token: Callable[[], str | None]
    verify_token: Callable[..., object]


class TushareSdkRealtimeClient:
    """Minimal adapter around the Human Owner-approved Tushare SDK route."""

    def __init__(
        self,
        importer: ModuleImporter = importlib.import_module,
    ) -> None:
        self._sdk = cast(TushareSdkModule, importer("tushare"))
        self._constants = cast(
            TushareConstantsModule,
            importer("tushare.stock.cons"),
        )
        self._verify_token = cast(
            TushareVerifyTokenModule,
            importer("tushare.util.verify_token"),
        )
        self._token: str | None = None
        self._verify_url: str | None = None
        self.version = self._sdk.__version__

    def configure(self, token: str, verify_url: str) -> None:
        # tushare.set_token() writes ~/tk.csv. StockWatcher must keep credentials
        # exclusively in the platform credential store, so the SDK receives the token
        # only inside the serialized call below.
        self._token = token
        self._verify_url = verify_url

    def fetch(
        self,
        codes: tuple[str, ...],
        *,
        source: str,
    ) -> list[dict[str, object]] | None:
        token = self._token
        verify_url = self._verify_url
        if not token or not verify_url:
            raise RuntimeError("native realtime client is not configured")
        # The SDK may print supplier messages. They are intentionally discarded because
        # upstream text can contain identifiers or implementation details.
        with SDK_RUNTIME_LOCK:
            previous_get_token = self._verify_token.get_token
            previous_verify_token = self._verify_token.verify_token
            previous_verify_url = self._constants.verify_token_url

            def checked_verify_token(*args: object, **kwargs: object) -> object:
                response = previous_verify_token(*args, **kwargs)
                status_code = getattr(response, "status_code", None)
                if status_code in {401, 403, 429, 500, 502, 503, 504}:
                    # The SDK's permission decorator can otherwise replace an
                    # HTTP failure with an AttributeError while formatting its
                    # response. Preserve only the non-sensitive status marker
                    # so the transport can apply the correct safe policy.
                    raise RuntimeError(f"HTTP {status_code}")
                return response

            try:
                self._verify_token.get_token = lambda: token
                self._verify_token.verify_token = checked_verify_token
                self._constants.verify_token_url = verify_url
                with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                    frame = self._sdk.realtime_quote(
                        ts_code=",".join(codes),
                        src=source,
                    )
            finally:
                self._verify_token.get_token = previous_get_token
                self._verify_token.verify_token = previous_verify_token
                self._constants.verify_token_url = previous_verify_url
        if frame is None:
            return None
        rows = frame.to_dict(orient="records")
        return rows


class NativeRealtimeTransport:
    """Batched realtime snapshot transport using Tushare's native Sina route.

    This is a distinct provider profile. It never silently falls back from Super/Fast,
    never mixes another realtime source into the same batch, and keeps every supplier
    timestamp on the normalized row.
    """

    profile_name = "native_realtime"
    schema_version = "native-realtime-v1"

    def __init__(
        self,
        profile: NativeRealtimeProfile,
        secret_getter: SecretGetter,
        *,
        client: NativeRealtimeClient | None = None,
        clock: Clock | None = None,
        monotonic: Monotonic = time.monotonic,
        sleeper: Sleeper = time.sleep,
        request_budget: ApplicationRequestBudget | None = None,
    ) -> None:
        self.profile = profile
        self._secret_getter = secret_getter
        self._client = client or TushareSdkRealtimeClient()
        self._clock = clock or (lambda: datetime.now(SHANGHAI))
        self._monotonic = monotonic
        self._sleeper = sleeper
        self._lock = threading.Lock()
        self._request_budget = request_budget or ApplicationRequestBudget(
            profile.min_interval_seconds,
            clock=monotonic,
            sleeper=sleeper,
        )
        self.version = f"tushare-{self._client.version}"

    def execute(self, request: TransportRequest) -> TransportResult:
        if not request.realtime or request.api_name != "realtime_quote":
            raise ValueError("native realtime transport only accepts realtime_quote")
        codes = _validated_codes(request.params.get("ts_code"))
        token = self._secret_getter()
        if not token:
            raise ProviderError(ProviderFailureReason.CREDENTIAL_MISSING)

        started = self._monotonic()
        normalized: list[Record] = []
        with self._lock:
            self._client.configure(token, str(self.profile.verify_url).rstrip("/"))
            for offset in range(0, len(codes), self.profile.batch_size):
                batch = codes[offset : offset + self.profile.batch_size]
                self._request_budget.acquire("realtime")
                try:
                    rows = self._client.fetch(batch, source=self.profile.source)
                except Exception as exc:
                    reason = _safe_failure_reason(exc)
                    retry_after = (
                        exc.retry_after_seconds
                        if isinstance(exc, ProviderError)
                        else None
                    )
                    if reason is ProviderFailureReason.RATE_LIMITED:
                        retry_after = self._request_budget.pause_for(
                            retry_after,
                            lane="realtime",
                        )
                    raise ProviderError(
                        reason,
                        retry_after_seconds=retry_after,
                    ) from None
                if rows is None or not rows:
                    raise ProviderError(ProviderFailureReason.EMPTY_DATA)
                received = _as_shanghai(self._clock())
                normalized.extend(
                    _normalize_rows(
                        rows,
                        received=received,
                        profile=self.profile,
                        provider_version=self.version,
                    )
                )

        if not normalized:
            raise ProviderError(ProviderFailureReason.EMPTY_DATA)
        finished = _as_shanghai(self._clock())
        source_times = [
            parsed
            for record in normalized
            if isinstance(record.get("source_ts"), str)
            and (parsed := _parse_source_timestamp(record)) is not None
        ]
        worst_source = min(source_times) if source_times else None
        worst_freshness = (
            max(0.0, (finished - worst_source).total_seconds())
            if worst_source is not None
            else None
        )
        row_qualities = {record.get("data_quality") for record in normalized}
        quality = (
            DataQuality.HEALTHY
            if row_qualities == {DataQuality.HEALTHY.value}
            else DataQuality.STALE
            if DataQuality.STALE.value in row_qualities
            else DataQuality.DEGRADED
        )
        return TransportResult(
            records=tuple(normalized),
            http_status=200,
            elapsed_seconds=max(0.0, self._monotonic() - started),
            provenance=ProviderProvenance(
                provider_profile=self.profile_name,
                endpoint="tushare.realtime_quote:sina",
                provider_version=self.version,
                schema_version=self.schema_version,
                source_ts=worst_source,
                received_ts=finished,
                source_timestamp_kind=(
                    SourceTimestampKind.SUPPLIER
                    if worst_source is not None
                    else SourceTimestampKind.MISSING
                ),
                freshness_seconds=worst_freshness,
                quality=quality,
                degraded=quality is not DataQuality.HEALTHY,
                fields_used=request.fields,
            ),
        )

def _validated_codes(value: str | int | float | bool | None) -> tuple[str, ...]:
    if not isinstance(value, str):
        raise ValueError("native realtime request requires a comma-separated ts_code")
    codes = tuple(item.strip().upper() for item in value.split(",") if item.strip())
    if not codes or any(not CODE_PATTERN.fullmatch(code) for code in codes):
        raise ValueError("native realtime request contains an invalid ts_code")
    if len(codes) != len(set(codes)):
        raise ValueError("native realtime request contains duplicate ts_code values")
    return codes


def _normalize_rows(
    rows: list[dict[str, object]],
    *,
    received: datetime,
    profile: NativeRealtimeProfile,
    provider_version: str,
) -> list[Record]:
    normalized: list[Record] = []
    for row in rows:
        code = row.get("TS_CODE")
        if not isinstance(code, str) or not CODE_PATTERN.fullmatch(code.upper()):
            raise ProviderError(ProviderFailureReason.SCHEMA_CHANGED)
        source_ts = _source_timestamp(row)
        freshness = (
            max(0.0, (received - source_ts).total_seconds())
            if source_ts is not None
            else None
        )
        quality = (
            DataQuality.DEGRADED
            if source_ts is None
            else DataQuality.STALE
            if freshness is not None and freshness > profile.stale_after_seconds
            else DataQuality.HEALTHY
        )
        record: Record = {
            "ts_code": code.upper(),
            "name": _text(row.get("NAME")),
            "source_ts": source_ts.isoformat() if source_ts is not None else None,
            "received_ts": received.isoformat(),
            "freshness_seconds": round(freshness, 6) if freshness is not None else None,
            "provider_profile": "native_realtime",
            "endpoint": "tushare.realtime_quote:sina",
            "provider_version": provider_version,
            "schema_version": NativeRealtimeTransport.schema_version,
            "data_quality": quality.value,
            "volume_unit": "shares",
            "amount_unit": "CNY",
        }
        for source, target in NUMERIC_FIELDS.items():
            record[target] = _number(row.get(source))
        normalized.append(record)
    return normalized


def _source_timestamp(row: dict[str, object]) -> datetime | None:
    date = row.get("DATE")
    time_value = row.get("TIME")
    if not isinstance(date, str) or not isinstance(time_value, str):
        return None
    text = f"{date.strip()} {time_value.strip()}"
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y%m%d %H:%M:%S"):
        try:
            return datetime.strptime(text, pattern).replace(tzinfo=SHANGHAI)
        except ValueError:
            continue
    return None


def _parse_source_timestamp(record: Record) -> datetime | None:
    raw = record.get("source_ts")
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw).astimezone(SHANGHAI)
    except ValueError:
        return None


def _number(value: object) -> float | None:
    if value is None:
        return None
    scalar: str | int | float | bool
    if isinstance(value, (str, int, float, bool)):
        scalar = value
    else:
        item = getattr(value, "item", None)
        if not callable(item):
            return None
        converted = item()
        if not isinstance(converted, (str, int, float, bool)):
            return None
        scalar = converted
    try:
        number = float(scalar)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _as_shanghai(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=SHANGHAI)
    return value.astimezone(SHANGHAI)


def _safe_failure_reason(exc: Exception) -> ProviderFailureReason:
    if isinstance(exc, ProviderError):
        return exc.reason
    message = str(exc).casefold()
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status == 429 or any(
        marker in message
        for marker in (
            "429",
            "rate limit",
            "too many requests",
            "访问过于频繁",
            "次数超限",
            "暂时限流",
        )
    ):
        return ProviderFailureReason.RATE_LIMITED
    if status == 401 or "http 401" in message:
        return ProviderFailureReason.CREDENTIAL_INVALID
    if (
        isinstance(exc, PermissionError)
        or type(exc).__name__.casefold() == "permissionerror"
        or status == 403
        or "http 403" in message
    ):
        return ProviderFailureReason.PERMISSION_DENIED
    if status in {500, 502, 503, 504} or any(
        f"http {code}" in message for code in (500, 502, 503, 504)
    ):
        return ProviderFailureReason.SERVER_ERROR
    name = type(exc).__name__.casefold()
    if "timeout" in name:
        return ProviderFailureReason.TIMEOUT
    if any(marker in name for marker in ("connection", "request", "proxy")):
        return ProviderFailureReason.NETWORK
    return ProviderFailureReason.BUSINESS_ERROR
