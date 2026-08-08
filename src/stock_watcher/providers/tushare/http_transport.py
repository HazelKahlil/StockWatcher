from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests

from stock_watcher.config.data_sources import HttpProfile

from .errors import ProviderError, ProviderFailureReason
from .models import (
    DataQuality,
    ProviderProvenance,
    SourceTimestampKind,
    TransportResult,
)
from .rate_limit import ApplicationRequestBudget
from .response_parser import parse_tushare_payload
from .transport_protocol import TransportRequest

Clock = Callable[[], datetime]
SecretGetter = Callable[[], str | None]
Sleeper = Callable[[float], None]


class BaseHttpTransport:
    version = "tushare-http-v1"
    minimum_retry_interval_seconds = 0.25

    def __init__(
        self,
        profile: HttpProfile,
        secret_getter: SecretGetter,
        *,
        session: requests.Session | None = None,
        clock: Clock | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Sleeper = time.sleep,
        request_budget: ApplicationRequestBudget | None = None,
    ) -> None:
        self.profile = profile
        self.profile_name = profile.name
        self._secret_getter = secret_getter
        self._session = session or requests.Session()
        self._session.trust_env = profile.use_system_proxy
        self._clock = clock or (lambda: datetime.now(ZoneInfo("Asia/Shanghai")))
        self._monotonic = monotonic
        self._sleeper = sleeper
        # Product construction injects one shared budget.  A direct transport
        # remains safe on its own for diagnostics and isolated tests.
        self._request_budget = request_budget or ApplicationRequestBudget()

    def _url(self, endpoint: str) -> str:
        if not endpoint.startswith("/") or ".." in endpoint:
            raise ValueError("endpoint must be one safe absolute path")
        return urljoin(str(self.profile.base_url).rstrip("/") + "/", endpoint.lstrip("/"))

    def _secret(self) -> str:
        secret = self._secret_getter()
        if not secret:
            raise ProviderError(ProviderFailureReason.CREDENTIAL_MISSING)
        return secret

    def _request_parts(
        self, request: TransportRequest, secret: str
    ) -> tuple[dict[str, str], dict[str, object] | None]:
        raise NotImplementedError

    def execute(self, request: TransportRequest) -> TransportResult:
        secret = self._secret()
        headers, body = self._request_parts(request, secret)
        attempts = 1 if request.realtime else 3
        started = self._monotonic()
        response: requests.Response | None = None
        for attempt in range(attempts):
            self._request_budget.acquire("pro")
            try:
                timeout = (
                    self.profile.connect_timeout_seconds,
                    self.profile.read_timeout_seconds,
                )
                if request.method.upper() == "GET":
                    query = dict(request.params)
                    if request.fields:
                        query["fields"] = ",".join(request.fields)
                    response = self._session.request(
                        request.method,
                        self._url(request.endpoint),
                        headers=headers,
                        params=query or None,
                        timeout=timeout,
                    )
                else:
                    response = self._session.request(
                        request.method,
                        self._url(request.endpoint),
                        headers=headers,
                        json=body,
                        timeout=timeout,
                    )
            except requests.Timeout as exc:
                if attempt + 1 < attempts:
                    self._sleeper(
                        max(
                            self.minimum_retry_interval_seconds,
                            0.25 * (2**attempt),
                        )
                    )
                    continue
                raise ProviderError(ProviderFailureReason.TIMEOUT) from exc
            except requests.RequestException as exc:
                if attempt + 1 < attempts:
                    self._sleeper(
                        max(
                            self.minimum_retry_interval_seconds,
                            0.25 * (2**attempt),
                        )
                    )
                    continue
                raise ProviderError(ProviderFailureReason.NETWORK) from exc
            if response.status_code == 429:
                self._request_budget.pause_for(
                    _retry_after_seconds(response, now=self._clock()),
                    lane="pro",
                )
                break
            if response.status_code in {500, 502, 503, 504} and attempt + 1 < attempts:
                self._sleeper(
                    max(
                        self.minimum_retry_interval_seconds,
                        _retry_delay(response, attempt),
                    )
                )
                continue
            break
        assert response is not None
        _raise_for_status(response, now=self._clock())
        try:
            payload: Any = response.json()
        except requests.exceptions.JSONDecodeError as exc:
            raise ProviderError(ProviderFailureReason.INVALID_JSON) from exc
        parsed = parse_tushare_payload(payload, allow_empty=request.allow_empty)
        received = self._clock()
        source_ts, timestamp_kind = _extract_source_timestamp(parsed.records)
        quality = DataQuality.HEALTHY if source_ts is not None else DataQuality.DEGRADED
        freshness = (
            max(0.0, (received - source_ts).total_seconds()) if source_ts is not None else None
        )
        return TransportResult(
            records=parsed.records,
            http_status=response.status_code,
            elapsed_seconds=max(0.0, self._monotonic() - started),
            provenance=ProviderProvenance(
                provider_profile=self.profile_name,
                endpoint=request.endpoint,
                provider_version=self.version,
                schema_version="v1",
                source_ts=source_ts,
                received_ts=received,
                source_timestamp_kind=timestamp_kind,
                freshness_seconds=freshness,
                quality=quality,
                degraded=quality is not DataQuality.HEALTHY,
                fields_used=request.fields,
            ),
        )


def _retry_delay(response: requests.Response, attempt: int) -> float:
    if response.status_code == 429:
        return _retry_after_seconds(response)
    return 0.25 * float(2**attempt)


def _retry_after_seconds(
    response: requests.Response,
    *,
    now: datetime | None = None,
) -> float:
    retry_after_raw = response.headers.get("Retry-After")
    if retry_after_raw:
        retry_after = str(retry_after_raw)
        try:
            return max(0.0, float(retry_after))
        except ValueError:
            try:
                parsed = parsedate_to_datetime(retry_after)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=ZoneInfo("UTC"))
                reference = now or datetime.now(ZoneInfo("UTC"))
                if reference.tzinfo is None:
                    reference = reference.replace(tzinfo=ZoneInfo("UTC"))
                return max(0.0, (parsed - reference).total_seconds())
            except (TypeError, ValueError, OverflowError):
                pass
    # The supplier frequently omits Retry-After.  A short exponential retry
    # simply recreates the burst that triggered the 429, so use the documented
    # application cooldown instead.
    return ApplicationRequestBudget.default_rate_limit_cooldown_seconds


def _raise_for_status(response: requests.Response, *, now: datetime | None = None) -> None:
    status = response.status_code
    if status == 401:
        raise ProviderError(ProviderFailureReason.CREDENTIAL_INVALID, http_status=status)
    if status == 403:
        raise ProviderError(ProviderFailureReason.PERMISSION_DENIED, http_status=status)
    if status == 429:
        retry_after = _retry_after_seconds(response, now=now)
        raise ProviderError(
            ProviderFailureReason.RATE_LIMITED,
            http_status=status,
            retry_after_seconds=retry_after,
        )
    if status == 503:
        raise ProviderError(ProviderFailureReason.FRESHNESS, http_status=status)
    if status in {500, 502, 504}:
        raise ProviderError(ProviderFailureReason.SERVER_ERROR, http_status=status)
    if status >= 400:
        raise ProviderError(ProviderFailureReason.BUSINESS_ERROR, http_status=status)


def _extract_source_timestamp(
    records: tuple[dict[str, str | int | float | bool | None], ...],
) -> tuple[datetime | None, SourceTimestampKind]:
    if not records:
        return None, SourceTimestampKind.MISSING
    for field in ("source_ts", "trade_time", "time", "datetime", "timestamp"):
        value = records[0].get(field)
        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                continue
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
            return parsed.astimezone(ZoneInfo("Asia/Shanghai")), SourceTimestampKind.SUPPLIER
    return None, SourceTimestampKind.MISSING
