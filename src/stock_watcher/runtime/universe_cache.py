from __future__ import annotations

import hashlib
import hmac
import json
import os
import shutil
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, ValidationError

from stock_watcher.domain import (
    SHANGHAI,
    DataQuality,
    SectorMembership,
    Security,
    SourceTimestampKind,
)
from stock_watcher.engine import (
    FundCapability,
    FundCapabilityResult,
    SecurityProfile,
    ThreeDayTrend,
)

if TYPE_CHECKING:
    from .tushare_runtime import RuntimeUniverse

RUNTIME_UNIVERSE_CACHE_VERSION = "runtime-universe-v1"


class UniverseCacheFailure(StrEnum):
    MISSING = "missing"
    CORRUPT = "corrupt"
    STALE = "stale"
    INCOMPLETE = "incomplete"
    IO = "io"


class UniverseCacheError(RuntimeError):
    def __init__(self, reason: UniverseCacheFailure) -> None:
        super().__init__(reason.value)
        self.reason = reason


class _CacheModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _SecurityRecord(_CacheModel):
    code: str
    name: str
    market: str


class _ProfileRecord(_CacheModel):
    security: _SecurityRecord
    listed_trading_days: int
    is_st: bool
    is_delisting: bool
    is_corporate_action_day: bool


class _MembershipRecord(_CacheModel):
    security: _SecurityRecord
    sector_code: str
    sector_name: str
    sector_type: str
    member_count: int
    effective_date: date
    source_ts: datetime
    received_ts: datetime
    provider_version: str
    config_version: str
    quality: DataQuality
    source_timestamp_kind: SourceTimestampKind


class _TrendRecord(_CacheModel):
    cumulative_change_pct: float
    highs_rising: bool
    lows_rising: bool
    amount_rising: bool
    highest_price: float | None


class _FundRecord(_CacheModel):
    capability: FundCapability
    reason: str
    fields: tuple[str, ...]


class _UniverseRecord(_CacheModel):
    profiles: tuple[_ProfileRecord, ...]
    memberships: tuple[_MembershipRecord, ...]
    trends: dict[str, _TrendRecord]
    high_3d: dict[str, float]
    open_dates: tuple[date, ...]
    concept_loaded: bool
    fund_capability: _FundRecord


class _UnsignedDocument(_CacheModel):
    schema_version: str
    generated_at: datetime
    trend_through_date: date
    universe: _UniverseRecord


class _Document(_UnsignedDocument):
    sha256: str


class RuntimeUniverseCache:
    """Credential-free, checksummed static context for critical realtime scans."""

    minimum_profile_count = 100
    maximum_age = timedelta(days=8)
    maximum_degraded_age = timedelta(days=30)

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(
        self,
        *,
        now: datetime,
        allow_stale: bool = False,
    ) -> RuntimeUniverse:
        if not self.path.is_file():
            raise UniverseCacheError(UniverseCacheFailure.MISSING)
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            document = _Document.model_validate(raw)
        except (OSError, json.JSONDecodeError, ValidationError):
            raise UniverseCacheError(UniverseCacheFailure.CORRUPT) from None
        unsigned = _UnsignedDocument(
            schema_version=document.schema_version,
            generated_at=document.generated_at,
            trend_through_date=document.trend_through_date,
            universe=document.universe,
        )
        expected = _digest(unsigned)
        if not hmac.compare_digest(document.sha256, expected):
            raise UniverseCacheError(UniverseCacheFailure.CORRUPT)
        if document.schema_version != RUNTIME_UNIVERSE_CACHE_VERSION:
            raise UniverseCacheError(UniverseCacheFailure.STALE)
        universe = _to_universe(unsigned)
        _validate_structure(universe, minimum_profiles=self.minimum_profile_count)
        _validate_freshness(
            universe,
            now=_shanghai(now),
            maximum_age=(
                self.maximum_degraded_age if allow_stale else self.maximum_age
            ),
            require_expected_trend=not allow_stale,
        )
        return universe

    def install_seed(self, seed_path: Path, *, now: datetime) -> bool:
        """Install one validated non-secret seed only when the user cache is absent.

        The seed is a build artifact, not a tracked repository file.  It lets a
        newly installed internal App start realtime observation immediately,
        while the ordinary Pro route refreshes stock/sector/trend context in the
        background.  Existing user cache is never overwritten.
        """
        if self.path.exists() or not seed_path.is_file():
            return False
        seed = RuntimeUniverseCache(seed_path)
        seed.load(now=_shanghai(now), allow_stale=True)
        temporary = self.path.with_suffix(self.path.suffix + ".seed.tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(seed_path, temporary)
            os.chmod(temporary, 0o600)
            temporary.replace(self.path)
        except OSError:
            temporary.unlink(missing_ok=True)
            raise UniverseCacheError(UniverseCacheFailure.IO) from None
        return True

    def save_preserving_last_known_good(
        self,
        fresh: RuntimeUniverse,
        previous: RuntimeUniverse | None,
    ) -> bool:
        """Atomically replace the cache, except when concepts just failed.

        A failed concept load must never overwrite a verified concept-enabled
        cache with an industry-only version.  Returns True when the previous
        on-disk cache was preserved (last-known-good).
        """
        if fresh.concept_loaded or previous is None or not previous.concept_loaded:
            self.save(fresh)
            return False
        return True

    def save(self, universe: RuntimeUniverse) -> None:
        _validate_structure(universe, minimum_profiles=self.minimum_profile_count)
        if universe.generated_at is None or universe.trend_through_date is None:
            raise UniverseCacheError(UniverseCacheFailure.INCOMPLETE)
        unsigned = _from_universe(universe)
        document = _Document(
            **unsigned.model_dump(),
            sha256=_digest(unsigned),
        )
        rendered = json.dumps(
            document.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(rendered + "\n", encoding="utf-8")
            os.chmod(temporary, 0o600)
            temporary.replace(self.path)
        except OSError:
            raise UniverseCacheError(UniverseCacheFailure.IO) from None


def universe_is_current(universe: RuntimeUniverse, *, now: datetime) -> bool:
    try:
        _validate_structure(
            universe,
            minimum_profiles=RuntimeUniverseCache.minimum_profile_count,
        )
        _validate_freshness(
            universe,
            now=_shanghai(now),
            maximum_age=RuntimeUniverseCache.maximum_age,
            require_expected_trend=True,
        )
    except UniverseCacheError:
        return False
    return True


def universe_is_usable(universe: RuntimeUniverse, *, now: datetime) -> bool:
    """Whether verified static context is safe for degraded realtime use."""
    try:
        _validate_structure(
            universe,
            minimum_profiles=RuntimeUniverseCache.minimum_profile_count,
        )
        _validate_freshness(
            universe,
            now=_shanghai(now),
            maximum_age=RuntimeUniverseCache.maximum_degraded_age,
            require_expected_trend=False,
        )
    except UniverseCacheError:
        return False
    return True


def _digest(unsigned: _UnsignedDocument) -> str:
    canonical = json.dumps(
        unsigned.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _from_universe(universe: RuntimeUniverse) -> _UnsignedDocument:
    assert universe.generated_at is not None
    assert universe.trend_through_date is not None
    return _UnsignedDocument(
        schema_version=RUNTIME_UNIVERSE_CACHE_VERSION,
        generated_at=universe.generated_at,
        trend_through_date=universe.trend_through_date,
        universe=_UniverseRecord(
            profiles=tuple(
                _ProfileRecord(
                    security=_security_record(profile.security),
                    listed_trading_days=profile.listed_trading_days,
                    is_st=profile.is_st,
                    is_delisting=profile.is_delisting,
                    is_corporate_action_day=profile.is_corporate_action_day,
                )
                for profile in universe.profiles
            ),
            memberships=tuple(
                _MembershipRecord(
                    security=_security_record(membership.security),
                    sector_code=membership.sector_code,
                    sector_name=membership.sector_name,
                    sector_type=membership.sector_type,
                    member_count=membership.member_count,
                    effective_date=membership.effective_date,
                    source_ts=membership.source_ts,
                    received_ts=membership.received_ts,
                    provider_version=membership.provider_version,
                    config_version=membership.config_version,
                    quality=membership.quality,
                    source_timestamp_kind=membership.source_timestamp_kind,
                )
                for membership in universe.memberships
            ),
            trends={
                code: _TrendRecord(
                    cumulative_change_pct=trend.cumulative_change_pct,
                    highs_rising=trend.highs_rising,
                    lows_rising=trend.lows_rising,
                    amount_rising=trend.amount_rising,
                    highest_price=trend.highest_price,
                )
                for code, trend in universe.trends.items()
            },
            high_3d=universe.high_3d,
            open_dates=universe.open_dates,
            concept_loaded=universe.concept_loaded,
            fund_capability=_FundRecord(
                capability=universe.fund_capability.capability,
                reason=universe.fund_capability.reason,
                fields=universe.fund_capability.fields,
            ),
        ),
    )


def _to_universe(document: _UnsignedDocument) -> RuntimeUniverse:
    from .tushare_runtime import RuntimeUniverse

    profiles = tuple(
        SecurityProfile(
            security=_security(profile.security),
            listed_trading_days=profile.listed_trading_days,
            is_st=profile.is_st,
            is_delisting=profile.is_delisting,
            is_corporate_action_day=profile.is_corporate_action_day,
        )
        for profile in document.universe.profiles
    )
    memberships = tuple(
        SectorMembership(
            security=_security(membership.security),
            sector_code=membership.sector_code,
            sector_name=membership.sector_name,
            sector_type=membership.sector_type,
            member_count=membership.member_count,
            effective_date=membership.effective_date,
            source_ts=_shanghai(membership.source_ts),
            received_ts=_shanghai(membership.received_ts),
            provider_version=membership.provider_version,
            config_version=membership.config_version,
            quality=membership.quality,
            source_timestamp_kind=membership.source_timestamp_kind,
        )
        for membership in document.universe.memberships
    )
    trends = {
        code: ThreeDayTrend(
            cumulative_change_pct=trend.cumulative_change_pct,
            highs_rising=trend.highs_rising,
            lows_rising=trend.lows_rising,
            amount_rising=trend.amount_rising,
            highest_price=trend.highest_price,
        )
        for code, trend in document.universe.trends.items()
    }
    fund = document.universe.fund_capability
    return RuntimeUniverse(
        profiles=profiles,
        memberships=memberships,
        trends=trends,
        high_3d=document.universe.high_3d,
        open_dates=document.universe.open_dates,
        concept_loaded=document.universe.concept_loaded,
        fund_capability=FundCapabilityResult(
            fund.capability,
            fund.reason,
            fund.fields,
        ),
        generated_at=_shanghai(document.generated_at),
        trend_through_date=document.trend_through_date,
    )


def _validate_structure(
    universe: RuntimeUniverse,
    *,
    minimum_profiles: int,
) -> None:
    profile_codes = tuple(profile.security.code for profile in universe.profiles)
    membership_codes = {membership.security.code for membership in universe.memberships}
    if (
        len(profile_codes) < minimum_profiles
        or len(profile_codes) != len(set(profile_codes))
        or len(membership_codes) < minimum_profiles
        or len(universe.trends) < minimum_profiles
        or len(universe.high_3d) < minimum_profiles
        or len(universe.open_dates) < 4
        or tuple(sorted(set(universe.open_dates))) != universe.open_dates
        or universe.trend_through_date not in universe.open_dates
    ):
        raise UniverseCacheError(UniverseCacheFailure.INCOMPLETE)
    valid_codes = set(profile_codes)
    if (
        not membership_codes <= valid_codes
        or not set(universe.trends) <= valid_codes
        or not set(universe.high_3d) <= valid_codes
        or any(membership.member_count < 3 for membership in universe.memberships)
    ):
        raise UniverseCacheError(UniverseCacheFailure.INCOMPLETE)
    if universe.concept_loaded and not any(
        membership.sector_type == "concept" for membership in universe.memberships
    ):
        raise UniverseCacheError(UniverseCacheFailure.INCOMPLETE)


def _validate_freshness(
    universe: RuntimeUniverse,
    *,
    now: datetime,
    maximum_age: timedelta,
    require_expected_trend: bool,
) -> None:
    generated_at = universe.generated_at
    trend_through = universe.trend_through_date
    if generated_at is None or trend_through is None:
        raise UniverseCacheError(UniverseCacheFailure.INCOMPLETE)
    age = now - generated_at
    if age < -timedelta(minutes=5) or age > maximum_age:
        raise UniverseCacheError(UniverseCacheFailure.STALE)
    if require_expected_trend:
        expected = _expected_trend_through(universe.open_dates, now)
        if expected is None or trend_through != expected:
            raise UniverseCacheError(UniverseCacheFailure.STALE)


def _expected_trend_through(
    open_dates: tuple[date, ...],
    now: datetime,
) -> date | None:
    include_today = now.timetz().replace(tzinfo=None) >= time(15, 30)
    eligible = tuple(
        day
        for day in open_dates
        if day < now.date() or (include_today and day == now.date())
    )
    return max(eligible, default=None)


def _security_record(security: Security) -> _SecurityRecord:
    return _SecurityRecord(
        code=security.code,
        name=security.name,
        market=security.market,
    )


def _security(record: _SecurityRecord) -> Security:
    return Security(record.code, record.name, record.market)


def _shanghai(value: datetime) -> datetime:
    return value.replace(tzinfo=SHANGHAI) if value.tzinfo is None else value.astimezone(SHANGHAI)
