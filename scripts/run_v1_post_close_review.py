from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Protocol

from stock_watcher.config import DataSourceMode, DataSourceSettings
from stock_watcher.domain import SHANGHAI
from stock_watcher.engine import build_post_close_review
from stock_watcher.providers.tushare import (
    ApplicationRequestBudget,
    ProProxyTransport,
    Tushare15000Provider,
)
from stock_watcher.providers.tushare.capability_router import CapabilityRouter
from stock_watcher.providers.tushare.errors import ProviderError
from stock_watcher.providers.tushare.models import TransportResult
from stock_watcher.providers.tushare.native_realtime_transport import (
    NativeRealtimeTransport,
)
from stock_watcher.providers.tushare.provider import TushareProvider
from stock_watcher.providers.tushare.super_transport import SuperTransport
from stock_watcher.security import (
    PRIMARY_CREDENTIAL,
    SUPER_CREDENTIAL,
    CredentialRef,
    CredentialStore,
    KeyringCredentialStore,
)
from stock_watcher.storage import SQLiteStore


class CloseDataProvider(Protocol):
    def stock_list(self, **params: str | int | float | bool) -> TransportResult: ...

    def trading_dates(
        self,
        **params: str | int | float | bool,
    ) -> TransportResult: ...

    def daily_bars(self, **params: str | int | float | bool) -> TransportResult: ...


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build a close-only StockWatcher review from real Tushare daily data; "
            "this does not claim intraday validation"
        )
    )
    parser.add_argument(
        "--trade-date",
        type=_trade_date,
        default=datetime.now(SHANGHAI).date(),
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--database", type=Path)
    parser.add_argument(
        "--advanced-super-diagnostic-fallback",
        action="store_true",
        help=(
            "Use the retained Super static-data diagnostic route when the "
            "ordinary fastapic route is rate-limited; never a production claim"
        ),
    )
    args = parser.parse_args()

    store = KeyringCredentialStore()
    advanced_diagnostic = bool(args.advanced_super_diagnostic_fallback)
    if advanced_diagnostic:
        reference = SUPER_CREDENTIAL if store.get(SUPER_CREDENTIAL) else None
    else:
        reference = _primary_credential_reference(store)
    if reference is None:
        print(f"{_storage_label(store)}中没有已保存的统一 Tushare Token。")
        return 2
    settings = DataSourceSettings()

    def secret_getter() -> str | None:
        return store.get(reference)

    product_provider: Tushare15000Provider | None = None
    if advanced_diagnostic:
        super_transport = SuperTransport(settings.super_profile, secret_getter)
        provider: CloseDataProvider = TushareProvider(
            CapabilityRouter(
                super_transport,
                super_transport,
                mode=DataSourceMode.SUPER,
            )
        )
    else:
        product_provider, request_budget = _build_product_provider(settings, secret_getter)
        provider = product_provider
    trade_date: date = args.trade_date
    compact = trade_date.strftime("%Y%m%d")
    try:
        stocks = provider.stock_list(exchange="", list_status="L")
        target_daily = provider.daily_bars(trade_date=compact)
    except ProviderError as error:
        print(f"盘后核心数据读取未通过：{error.reason.value}")
        return 1
    except Exception:
        print("盘后核心数据读取未通过：provider")
        return 1

    optional_failures: list[str] = []
    open_dates: tuple[date, ...] = (trade_date,)
    try:
        calendar = provider.trading_dates(
            exchange="SSE",
            start_date=(trade_date - timedelta(days=30)).strftime("%Y%m%d"),
            end_date=compact,
            is_open="1",
        )
        parsed_open_dates = tuple(
            sorted(
                {
                    parsed
                    for row in calendar.records
                    if (parsed := _compact_date(row.get("cal_date"))) is not None
                    and str(row.get("is_open", "")).casefold()
                    in {"1", "true", "y", "yes"}
                }
            )
        )
        if trade_date in parsed_open_dates:
            open_dates = parsed_open_dates
        else:
            optional_failures.append("trade_calendar")
    except ProviderError:
        optional_failures.append("trade_calendar")
    except Exception:
        optional_failures.append("trade_calendar")

    review_dates = open_dates[-4:]
    daily_by_date = {trade_date: target_daily.records}
    for day in review_dates:
        if day == trade_date:
            continue
        try:
            daily_by_date[day] = provider.daily_bars(
                trade_date=day.strftime("%Y%m%d")
            ).records
        except Exception:
            optional_failures.append(f"daily:{day.isoformat()}")
            break
    moneyflow_records: tuple[dict[str, str | int | float | bool | None], ...] = ()
    if product_provider is not None:
        try:
            moneyflow_records = product_provider.moneyflow(
                trade_date=compact
            ).records
        except Exception:
            optional_failures.append("moneyflow")
    else:
        optional_failures.append("moneyflow:advanced-diagnostic-unavailable")
    previous_adjustment_records: tuple[
        dict[str, str | int | float | bool | None], ...
    ] = ()
    current_adjustment_records: tuple[
        dict[str, str | int | float | bool | None], ...
    ] = ()
    if len(review_dates) >= 2 and product_provider is not None:
        try:
            previous_adjustment_records = product_provider.adjustment_factors(
                trade_date=review_dates[-2].strftime("%Y%m%d")
            ).records
            current_adjustment_records = product_provider.adjustment_factors(
                trade_date=compact
            ).records
        except Exception:
            optional_failures.append("adjustment_factor")
    elif product_provider is None:
        optional_failures.append("adjustment_factor:advanced-diagnostic-unavailable")

    mechanical_codes = _mechanical_jump_codes(
        previous_adjustment_records,
        current_adjustment_records,
    )
    generated_at = datetime.now(SHANGHAI)
    review = build_post_close_review(
        trade_date=trade_date,
        generated_at=generated_at,
        stock_records=stocks.records,
        daily_records_by_date=daily_by_date,
        open_dates=open_dates,
        moneyflow_records=moneyflow_records,
        mechanical_jump_codes=frozenset(mechanical_codes),
    )
    record = review.as_record()
    record["credential_source"] = (
        "platform_secure_storage_super_advanced_diagnostic"
        if advanced_diagnostic
        else "platform_secure_storage_primary"
    )
    record["credential_storage"] = _storage_label(store)
    record["provider_route"] = (
        "https://ai-tool.indevs.in/tushare/pro (advanced diagnostic only)"
        if advanced_diagnostic
        else "https://fastapic.stockai888.top"
    )
    if advanced_diagnostic:
        limitations = record.get("data_limitations")
        if isinstance(limitations, (list, tuple)):
            record["data_limitations"] = [
                *limitations,
                "本次因主路线限频使用旧Super静态高级诊断；不构成V1主数据路线验收。"
            ]
    record["source_coverage"] = {
        "stock_records": len(stocks.records),
        "daily_records": {
            day.isoformat(): len(rows) for day, rows in daily_by_date.items()
        },
        "moneyflow_records": len(moneyflow_records),
        "open_dates_checked": len(open_dates),
        "mechanical_jump_exclusions": len(mechanical_codes),
        "optional_failures": optional_failures,
    }
    if not advanced_diagnostic:
        record["request_budget"] = {
            "shared_across_pro_and_realtime": True,
            "request_start_interval_seconds": request_budget.min_interval_seconds,
            "default_429_cooldown_seconds": (
                ApplicationRequestBudget.default_rate_limit_cooldown_seconds
            ),
        }
    record["minute_history_probe"] = {
        "trade_date": trade_date.isoformat(),
        "status": "unavailable",
        "used_in_review": False,
        "reason": "当日stk_mins受控探测返回业务不可用",
    }
    rendered = json.dumps(record, ensure_ascii=False, indent=2) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8", newline="\n")
    if args.database is not None:
        args.database.parent.mkdir(parents=True, exist_ok=True)
        database = SQLiteStore(args.database)
        database.initialize()
        summary_record = review.daily_summary_record()
        if advanced_diagnostic:
            summary_record["health_summary"] = (
                f"{summary_record['health_summary']}"
                " 本次数据来自旧Super静态高级诊断，不构成主路线验收。"
            )
        database.record_daily_summary(summary_record)
    digest = hashlib.sha256(args.output.read_bytes()).hexdigest()
    print(f"report={args.output}")
    if args.database is not None:
        print(f"database={args.database}")
    print(f"sha256={digest}")
    print(f"top3={len(review.top3)}")
    print("verdict=RETROSPECTIVE_ONLY")
    return 0


def _build_product_provider(
    settings: DataSourceSettings,
    secret_getter: Callable[[], str | None],
) -> tuple[Tushare15000Provider, ApplicationRequestBudget]:
    budget = ApplicationRequestBudget(settings.request_budget_interval_seconds)
    pro = ProProxyTransport(
        settings.primary_profile,
        secret_getter,
        request_budget=budget,
    )
    native = NativeRealtimeTransport(
        settings.native_realtime_profile,
        secret_getter,
        request_budget=budget,
    )
    return Tushare15000Provider(pro, native), budget


def _primary_credential_reference(store: CredentialStore) -> CredentialRef | None:
    return PRIMARY_CREDENTIAL if store.get(PRIMARY_CREDENTIAL) else None


def _storage_label(store: object) -> str:
    label = getattr(store, "storage_label", None)
    return label if isinstance(label, str) and label else "系统安全存储"


def _trade_date(value: str) -> date:
    compact = value.replace("-", "")
    try:
        return datetime.strptime(compact, "%Y%m%d").date()
    except ValueError as error:
        raise argparse.ArgumentTypeError("trade date must be YYYY-MM-DD") from error


def _compact_date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    compact = value.replace("-", "")
    try:
        return datetime.strptime(compact, "%Y%m%d").date()
    except ValueError:
        return None


def _mechanical_jump_codes(
    previous: tuple[dict[str, str | int | float | bool | None], ...],
    current: tuple[dict[str, str | int | float | bool | None], ...],
) -> set[str]:
    previous_values = {
        str(row.get("ts_code")): _number(row.get("adj_factor"))
        for row in previous
        if row.get("ts_code")
    }
    return {
        code
        for row in current
        if (code := str(row.get("ts_code") or ""))
        and code in previous_values
        and _number(row.get("adj_factor")) != previous_values[code]
    }


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return 0.0
    try:
        return float(value)
    except ValueError:
        return 0.0


if __name__ == "__main__":
    raise SystemExit(main())
