from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from .errors import ProviderError, ProviderFailureReason
from .models import Record


@dataclass(frozen=True, slots=True)
class NormalizedStock:
    code: str
    name: str
    market: str
    list_status: str | None
    is_st: bool


@dataclass(frozen=True, slots=True)
class NormalizedBar:
    code: str
    source_ts: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    amount: Decimal
    volume_unit: str
    amount_unit: str


def _required_text(record: Record, field: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ProviderError(ProviderFailureReason.SCHEMA_CHANGED)
    return value.strip()


def normalize_stock_records(records: tuple[Record, ...]) -> tuple[NormalizedStock, ...]:
    normalized: list[NormalizedStock] = []
    seen: set[str] = set()
    for record in records:
        code = _required_text(record, "ts_code")
        name = _required_text(record, "name")
        if code in seen:
            raise ProviderError(ProviderFailureReason.SCHEMA_CHANGED)
        seen.add(code)
        suffix = code.rpartition(".")[2].upper()
        if suffix not in {"SH", "SZ", "BJ"}:
            raise ProviderError(ProviderFailureReason.SCHEMA_CHANGED)
        list_status_value = record.get("list_status")
        list_status = list_status_value if isinstance(list_status_value, str) else None
        upper_name = name.upper()
        normalized.append(
            NormalizedStock(
                code=code,
                name=name,
                market=suffix,
                list_status=list_status,
                is_st=upper_name.startswith("ST") or upper_name.startswith("*ST"),
            )
        )
    if not normalized:
        raise ProviderError(ProviderFailureReason.EMPTY_DATA)
    return tuple(normalized)


def _decimal(record: Record, field: str) -> Decimal:
    value = record.get(field)
    if isinstance(value, bool) or value is None:
        raise ProviderError(ProviderFailureReason.SCHEMA_CHANGED)
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ProviderError(ProviderFailureReason.SCHEMA_CHANGED) from exc
    if not result.is_finite():
        raise ProviderError(ProviderFailureReason.SCHEMA_CHANGED)
    return result


def _timestamp(record: Record) -> datetime:
    raw = record.get("source_ts") or record.get("trade_time") or record.get("trade_date")
    if not isinstance(raw, str):
        raise ProviderError(ProviderFailureReason.SCHEMA_CHANGED)
    cleaned = raw.strip()
    if len(cleaned) == 8 and cleaned.isdigit():
        cleaned = f"{cleaned[:4]}-{cleaned[4:6]}-{cleaned[6:]}T00:00:00"
    try:
        parsed = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProviderError(ProviderFailureReason.SCHEMA_CHANGED) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    return parsed.astimezone(ZoneInfo("Asia/Shanghai"))


def normalize_bar_records(
    records: tuple[Record, ...],
    *,
    volume_unit: str | None,
    amount_unit: str | None,
) -> tuple[NormalizedBar, ...]:
    if not volume_unit or not amount_unit:
        raise ProviderError(ProviderFailureReason.SCHEMA_CHANGED)
    normalized: list[NormalizedBar] = []
    seen: set[tuple[str, datetime]] = set()
    for record in records:
        code = _required_text(record, "ts_code")
        source_ts = _timestamp(record)
        key = (code, source_ts)
        if key in seen:
            raise ProviderError(ProviderFailureReason.SCHEMA_CHANGED)
        seen.add(key)
        open_price = _decimal(record, "open")
        high = _decimal(record, "high")
        low = _decimal(record, "low")
        close = _decimal(record, "close")
        volume = _decimal(record, "vol")
        amount = _decimal(record, "amount")
        if (
            min(open_price, high, low, close) <= 0
            or high < max(open_price, low, close)
            or low > min(open_price, high, close)
            or volume < 0
            or amount < 0
        ):
            raise ProviderError(ProviderFailureReason.SCHEMA_CHANGED)
        normalized.append(
            NormalizedBar(
                code=code,
                source_ts=source_ts,
                open=open_price,
                high=high,
                low=low,
                close=close,
                volume=volume,
                amount=amount,
                volume_unit=volume_unit,
                amount_unit=amount_unit,
            )
        )
    if not normalized:
        raise ProviderError(ProviderFailureReason.EMPTY_DATA)
    return tuple(normalized)
