from __future__ import annotations

from collections.abc import Mapping, Sequence

from .errors import ProviderError, ProviderFailureReason
from .models import ParsedPayload, Record, Scalar


def _scalar(value: object) -> Scalar:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ProviderError(ProviderFailureReason.SCHEMA_CHANGED)


def _mapping_record(value: object) -> Record:
    if not isinstance(value, Mapping):
        raise ProviderError(ProviderFailureReason.SCHEMA_CHANGED)
    record: Record = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise ProviderError(ProviderFailureReason.SCHEMA_CHANGED)
        record[key] = _scalar(item)
    return record


def _records_from_fields_items(fields: object, items: object) -> tuple[Record, ...]:
    if not isinstance(fields, Sequence) or isinstance(fields, (str, bytes)):
        raise ProviderError(ProviderFailureReason.SCHEMA_CHANGED)
    if not isinstance(items, Sequence) or isinstance(items, (str, bytes)):
        raise ProviderError(ProviderFailureReason.SCHEMA_CHANGED)
    names = tuple(fields)
    if not all(isinstance(name, str) and name for name in names):
        raise ProviderError(ProviderFailureReason.SCHEMA_CHANGED)
    if len(set(names)) != len(names):
        raise ProviderError(ProviderFailureReason.SCHEMA_CHANGED)
    records: list[Record] = []
    for row in items:
        if not isinstance(row, Sequence) or isinstance(row, (str, bytes)):
            raise ProviderError(ProviderFailureReason.SCHEMA_CHANGED)
        if len(row) != len(names):
            raise ProviderError(ProviderFailureReason.SCHEMA_CHANGED)
        records.append({name: _scalar(item) for name, item in zip(names, row, strict=True)})
    return tuple(records)


def parse_tushare_payload(payload: object, *, allow_empty: bool = False) -> ParsedPayload:
    if not isinstance(payload, Mapping):
        raise ProviderError(ProviderFailureReason.SCHEMA_CHANGED)
    code = payload.get("code", 0)
    if isinstance(code, bool) or not isinstance(code, int):
        raise ProviderError(ProviderFailureReason.SCHEMA_CHANGED)
    if code != 0:
        raise ProviderError(ProviderFailureReason.BUSINESS_ERROR)
    data = payload.get("data")
    count_raw = payload.get("count")
    count = count_raw if isinstance(count_raw, int) and not isinstance(count_raw, bool) else None
    if isinstance(data, Mapping) and "fields" in data and "items" in data:
        records = _records_from_fields_items(data["fields"], data["items"])
    elif isinstance(data, Sequence) and not isinstance(data, (str, bytes)):
        records = tuple(_mapping_record(item) for item in data)
    elif isinstance(data, Mapping):
        records = (_mapping_record(data),)
    elif data is None:
        records = ()
    else:
        raise ProviderError(ProviderFailureReason.SCHEMA_CHANGED)
    if not records and not allow_empty:
        raise ProviderError(ProviderFailureReason.EMPTY_DATA)
    return ParsedPayload(
        records=records,
        count=count,
        code=code,
        message_present=payload.get("msg") is not None,
    )
