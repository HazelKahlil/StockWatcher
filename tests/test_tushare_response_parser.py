from __future__ import annotations

import pytest

from stock_watcher.providers.tushare import (
    ProviderError,
    ProviderFailureReason,
    parse_tushare_payload,
)


def test_parser_maps_fields_and_items_to_named_records() -> None:
    parsed = parse_tushare_payload(
        {
            "code": 0,
            "msg": None,
            "data": {
                "fields": ["ts_code", "trade_date", "close"],
                "items": [["ANON", "20260729", 12.5]],
            },
        }
    )
    assert parsed.records == (
        {"ts_code": "ANON", "trade_date": "20260729", "close": 12.5},
    )
    assert parsed.code == 0
    assert not parsed.message_present


@pytest.mark.parametrize(
    "data, expected",
    [
        ([{"name": "anonymous"}], ({"name": "anonymous"},)),
        ({"status": "ok"}, ({"status": "ok"},)),
    ],
)
def test_parser_accepts_list_or_mapping_data(
    data: object, expected: tuple[dict[str, object], ...]
) -> None:
    assert parse_tushare_payload({"code": 0, "data": data}).records == expected


@pytest.mark.parametrize(
    "payload",
    [
        {"code": 0, "data": None},
        {"code": 0, "data": []},
        {"code": 0, "data": {"fields": ["code"], "items": []}},
    ],
)
def test_parser_empty_data_fails_closed(payload: object) -> None:
    with pytest.raises(ProviderError) as captured:
        parse_tushare_payload(payload)
    assert captured.value.reason is ProviderFailureReason.EMPTY_DATA


def test_parser_allows_explicitly_safe_empty_health_response() -> None:
    parsed = parse_tushare_payload({"code": 0, "data": []}, allow_empty=True)
    assert parsed.records == ()


@pytest.mark.parametrize(
    "payload",
    [
        {"code": 0, "data": {"fields": ["a"], "items": [[1, 2]]}},
        {"code": 0, "data": {"fields": ["a", "a"], "items": [[1, 2]]}},
        {"code": 0, "data": {"fields": "a", "items": [[1]]}},
        {"code": "0", "data": []},
        {"code": 0, "data": "not-json-data"},
        {"code": 0, "data": [{"nested": {"not": "allowed"}}]},
    ],
)
def test_parser_schema_changes_fail_closed(payload: object) -> None:
    with pytest.raises(ProviderError) as captured:
        parse_tushare_payload(payload, allow_empty=True)
    assert captured.value.reason is ProviderFailureReason.SCHEMA_CHANGED


def test_parser_nonzero_supplier_code_is_business_error_without_message_leak() -> None:
    with pytest.raises(ProviderError) as captured:
        parse_tushare_payload(
            {"code": -1, "msg": "credential=must-not-leak", "data": None}
        )
    assert captured.value.reason is ProviderFailureReason.BUSINESS_ERROR
    assert "must-not-leak" not in str(captured.value)
