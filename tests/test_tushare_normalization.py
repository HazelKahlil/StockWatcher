from __future__ import annotations

from decimal import Decimal

import pytest

from stock_watcher.providers.tushare import ProviderError, ProviderFailureReason
from stock_watcher.providers.tushare.normalization import (
    normalize_bar_records,
    normalize_stock_records,
)


def test_stock_normalization_maps_markets_and_st_without_exposing_vendor_dicts() -> None:
    stocks = normalize_stock_records(
        (
            {"ts_code": "ANON1.SH", "name": "普通样本", "list_status": "L"},
            {"ts_code": "ANON2.SZ", "name": "*ST样本", "list_status": "L"},
            {"ts_code": "ANON3.BJ", "name": "北交样本", "list_status": "L"},
        )
    )
    assert [stock.market for stock in stocks] == ["SH", "SZ", "BJ"]
    assert [stock.is_st for stock in stocks] == [False, True, False]


@pytest.mark.parametrize(
    "records",
    [
        ({"ts_code": "ANON.SH", "name": "A"}, {"ts_code": "ANON.SH", "name": "B"}),
        ({"ts_code": "", "name": "A"},),
        ({"ts_code": "ANON.XX", "name": "A"},),
        ({"ts_code": "ANON.SH"},),
    ],
)
def test_stock_normalization_rejects_duplicates_and_missing_identity(
    records: tuple[dict[str, object], ...],
) -> None:
    with pytest.raises(ProviderError) as captured:
        normalize_stock_records(records)  # type: ignore[arg-type]
    assert captured.value.reason is ProviderFailureReason.SCHEMA_CHANGED


def test_bar_normalization_requires_explicit_units_and_shanghai_time() -> None:
    bars = normalize_bar_records(
        (
            {
                "ts_code": "ANON.SH",
                "source_ts": "2026-07-29T10:00:00+08:00",
                "open": 10,
                "high": 10.5,
                "low": 9.9,
                "close": 10.2,
                "vol": 100,
                "amount": 1020,
            },
        ),
        volume_unit="shares",
        amount_unit="CNY",
    )
    assert bars[0].close == Decimal("10.2")
    assert bars[0].source_ts.tzinfo is not None
    assert bars[0].volume_unit == "shares"


def test_bar_normalization_does_not_guess_units() -> None:
    with pytest.raises(ProviderError) as captured:
        normalize_bar_records((), volume_unit=None, amount_unit=None)
    assert captured.value.reason is ProviderFailureReason.SCHEMA_CHANGED


@pytest.mark.parametrize(
    "overrides",
    [
        {"high": 9.0},
        {"low": 11.0},
        {"vol": -1},
        {"amount": -1},
        {"source_ts": "not-a-time"},
    ],
)
def test_invalid_ohlc_time_or_units_fail_closed(overrides: dict[str, object]) -> None:
    record: dict[str, object] = {
        "ts_code": "ANON.SH",
        "source_ts": "2026-07-29T10:00:00+08:00",
        "open": 10,
        "high": 10.5,
        "low": 9.9,
        "close": 10.2,
        "vol": 100,
        "amount": 1020,
    }
    record.update(overrides)
    with pytest.raises(ProviderError) as captured:
        normalize_bar_records(
            (record,),  # type: ignore[arg-type]
            volume_unit="shares",
            amount_unit="CNY",
        )
    assert captured.value.reason is ProviderFailureReason.SCHEMA_CHANGED
