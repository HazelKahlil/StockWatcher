"""Independent Top3 repeat-occurrence tracker for Web observation hints.

This sidecar never participates in scoring, ranking, Stable Top3, level tags,
or strong-movement detection. The unit of counting is a distinct trade date.
"""
from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from stock_watcher.domain import SHANGHAI, HealthState
from stock_watcher.engine import Candidate, CandidateBatch
from stock_watcher.storage import SQLiteStore

WINDOW_DAYS = 14
ACTIVATE_AT = 3
REPEAT_BACKFILL_VERSION = 1
CODE_PATTERN = re.compile(r"^[0-9]{6}\.(?:SH|SZ|BJ)$")
NON_COUNTABLE_MARKERS = ("mock", "replay", "synthetic", "demo", "fixture")
SOURCE_AUTOMATIC = "automatic"
SOURCE_MANUAL = "manual"
SOURCE_SCHEDULED_0945 = "scheduled-09:45"
SOURCE_SCHEDULED_1445 = "scheduled-14:45"
SOURCE_INTRADAY = "intraday"
REPEAT_FIELDS = (
    "repeat_active",
    "repeat_count",
    "repeat_span_days",
    "repeat_label",
    "repeat_sequence_started_on",
    "repeat_activated_at",
    "repeat_last_seen_on",
)


def format_repeat_label(span_days: int, occurrence_count: int) -> str:
    return f"近{span_days}天第{occurrence_count}次"


def calendar_span_days(start: date, end: date) -> int:
    return (end - start).days + 1


def shanghai_trade_date(value: datetime) -> date:
    if value.tzinfo is None or getattr(value.tzinfo, "key", None) != SHANGHAI.key:
        raise ValueError("repeat occurrence timestamps must use the Asia/Shanghai timezone")
    return value.astimezone(SHANGHAI).date()


def parse_shanghai_timestamp(value: object) -> datetime | None:
    if isinstance(value, datetime):
        timestamp = value
    elif isinstance(value, str):
        try:
            timestamp = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    if timestamp.tzinfo is None:
        return None
    return timestamp.astimezone(SHANGHAI)


def is_valid_code(code: object) -> bool:
    return isinstance(code, str) and CODE_PATTERN.fullmatch(code) is not None


def provider_is_countable(provider_version: object) -> bool:
    text = str(provider_version or "").casefold()
    return bool(text) and not any(marker in text for marker in NON_COUNTABLE_MARKERS)


def empty_repeat_fields() -> dict[str, Any]:
    return {
        "repeat_active": False,
        "repeat_count": 0,
        "repeat_span_days": 0,
        "repeat_label": None,
        "repeat_sequence_started_on": None,
        "repeat_activated_at": None,
        "repeat_last_seen_on": None,
    }


@dataclass(frozen=True, slots=True)
class RepeatProjection:
    active: bool = False
    occurrence_count: int = 0
    span_days: int = 0
    sequence_started_on: date | None = None
    activated_at: str | None = None
    last_seen_on: date | None = None

    @property
    def label(self) -> str | None:
        if not self.active or self.occurrence_count < ACTIVATE_AT or self.span_days < 1:
            return None
        return format_repeat_label(self.span_days, self.occurrence_count)

    def as_fields(self) -> dict[str, Any]:
        return {
            "repeat_active": self.active,
            "repeat_count": self.occurrence_count,
            "repeat_span_days": self.span_days,
            "repeat_label": self.label,
            "repeat_sequence_started_on": (
                self.sequence_started_on.isoformat() if self.sequence_started_on else None
            ),
            "repeat_activated_at": self.activated_at,
            "repeat_last_seen_on": (
                self.last_seen_on.isoformat() if self.last_seen_on else None
            ),
        }


@dataclass(frozen=True, slots=True)
class RepeatBackfillReport:
    snapshots: int = 0
    occurrences: int = 0
    activated: int = 0
    skipped: int = 0


@dataclass(frozen=True, slots=True)
class _ComputedState:
    dates: tuple[date, ...]
    active: bool
    sequence_started_on: date | None
    occurrence_count: int
    span_days: int
    window_started_on: date | None
    window_expires_on: date | None
    newly_activated: bool


def compute_repeat_state(
    trade_dates: Sequence[date],
    *,
    previously_active: bool = False,
    sequence_started_on: date | None = None,
) -> _ComputedState:
    """Deterministic rolling 14-calendar-day / permanent-after-activation rule."""
    unique = tuple(sorted({item for item in trade_dates}))
    if not unique:
        return _ComputedState((), False, None, 0, 0, None, None, False)
    latest = unique[-1]
    if previously_active and sequence_started_on is not None:
        counted = tuple(item for item in unique if item >= sequence_started_on)
        last = counted[-1] if counted else latest
        span = calendar_span_days(sequence_started_on, last)
        expires = sequence_started_on + timedelta(days=WINDOW_DAYS - 1)
        return _ComputedState(
            counted,
            True,
            sequence_started_on,
            len(counted),
            span,
            sequence_started_on,
            expires,
            False,
        )
    window = tuple(
        item for item in unique if calendar_span_days(item, latest) <= WINDOW_DAYS
    )
    if not window:
        return _ComputedState((), False, None, 0, 0, None, None, False)
    started = window[0]
    expires = started + timedelta(days=WINDOW_DAYS - 1)
    span = calendar_span_days(started, latest)
    active = len(window) >= ACTIVATE_AT
    return _ComputedState(
        window,
        active,
        started,
        len(window),
        span,
        started,
        expires,
        newly_activated=active,
    )


class CandidateRepeatTracker:
    """Persist per-code trade-day occurrences and the current purple state."""

    def __init__(self, store: SQLiteStore) -> None:
        self.store = store

    def observe_batch_in(
        self,
        connection: sqlite3.Connection,
        *,
        batch: CandidateBatch,
        snapshot_id: int,
        seen_at: datetime,
        source_type: str,
    ) -> dict[str, RepeatProjection]:
        if not self._batch_is_countable(batch, seen_at):
            return self.projections_for_codes(
                connection,
                [candidate.code for candidate in batch.candidates],
            )
        trade_date = shanghai_trade_date(batch.source_ts)
        output: dict[str, RepeatProjection] = {}
        for candidate in batch.candidates[:3]:
            output[candidate.code] = self._observe_candidate(
                connection,
                candidate=candidate,
                trade_date=trade_date,
                snapshot_id=snapshot_id,
                seen_at=seen_at,
                source_type=source_type,
            )
        return output

    def note_source_in(
        self,
        connection: sqlite3.Connection,
        *,
        batch: CandidateBatch,
        snapshot_id: int,
        seen_at: datetime,
        source_type: str,
    ) -> dict[str, RepeatProjection]:
        """Merge a same-day source without creating a new occurrence."""
        if not batch.candidates:
            return {}
        try:
            trade_date = shanghai_trade_date(batch.source_ts)
        except ValueError:
            return self.projections_for_codes(
                connection,
                [candidate.code for candidate in batch.candidates],
            )
        output: dict[str, RepeatProjection] = {}
        for candidate in batch.candidates:
            if not is_valid_code(candidate.code):
                output[candidate.code] = RepeatProjection()
                continue
            existing = self._load_day(connection, candidate.code, trade_date)
            if existing is None:
                output[candidate.code] = self._load_projection(connection, candidate.code)
                continue
            self._touch_day(
                connection,
                existing=existing,
                name=candidate.name,
                snapshot_id=snapshot_id,
                seen_at=seen_at,
                source_type=source_type,
                formal_seen=candidate.is_formal,
                supplement_seen=candidate.is_supplement,
            )
            output[candidate.code] = self._load_projection(connection, candidate.code)
        return output

    def projections_for_codes(
        self,
        connection: sqlite3.Connection,
        codes: Iterable[str],
    ) -> dict[str, RepeatProjection]:
        return {code: self._load_projection(connection, code) for code in codes}

    def projections_from_store(self, codes: Iterable[str]) -> dict[str, RepeatProjection]:
        self.store.initialize()
        with self.store.connect() as connection:
            return self.projections_for_codes(connection, codes)

    def historical_fields_for(
        self,
        connection: sqlite3.Connection,
        *,
        code: str,
        trade_date: date,
    ) -> dict[str, Any]:
        row = self._load_day(connection, code, trade_date)
        if row is None:
            return empty_repeat_fields()
        active = bool(row["active_after"])
        count = int(row["count_after"])
        span = int(row["span_days_after"])
        sequence = trade_date - timedelta(days=span - 1) if span > 0 else None
        activated_at = None
        if active and count >= ACTIVATE_AT:
            state = self._load_state_row(connection, code)
            if state and state.get("activated_at"):
                activated_at = str(state["activated_at"])
            else:
                activated_at = str(row["first_seen_at"])
        projection = RepeatProjection(
            active=active,
            occurrence_count=count,
            span_days=span,
            sequence_started_on=sequence,
            activated_at=activated_at,
            last_seen_on=trade_date,
        )
        return projection.as_fields()

    def backfill(self) -> RepeatBackfillReport:
        """Replay countable snapshots in time order. Safe to run twice."""
        self.store.initialize()
        snapshots = 0
        skipped = 0
        with self.store.transaction(immediate=True) as connection:
            rows = self._load_backfill_rows(connection)
            grouped: dict[int, list[dict[str, Any]]] = {}
            order: list[int] = []
            for row in rows:
                snapshot_id = int(row["snapshot_id"])
                if snapshot_id not in grouped:
                    grouped[snapshot_id] = []
                    order.append(snapshot_id)
                grouped[snapshot_id].append(row)
            for snapshot_id in order:
                items = sorted(
                    grouped[snapshot_id],
                    key=lambda row: int(row["rank"]),
                )[:3]
                first = items[0]
                snapshots += 1
                seen_at = parse_shanghai_timestamp(first["source_ts"]) or parse_shanghai_timestamp(
                    first["generated_at"]
                )
                if (
                    str(first["health"]) != HealthState.HEALTHY.value
                    or seen_at is None
                    or not provider_is_countable(first["provider_version"])
                    or len(items) != 3
                ):
                    skipped += 1
                    continue
                for item in items:
                    if not is_valid_code(item["code"]):
                        skipped += 1
                        continue
                    candidate = _backfill_candidate(item, seen_at)
                    self._observe_candidate(
                        connection,
                        candidate=candidate,
                        trade_date=shanghai_trade_date(seen_at),
                        snapshot_id=snapshot_id,
                        seen_at=seen_at,
                        source_type=SOURCE_AUTOMATIC,
                    )
            for alert in self._load_backfill_alerts(connection):
                seen_at = parse_shanghai_timestamp(alert["displayed_at"])
                if seen_at is None or not is_valid_code(alert["code"]):
                    continue
                existing = self._load_day(
                    connection,
                    str(alert["code"]),
                    shanghai_trade_date(seen_at),
                )
                if existing is None:
                    continue
                self._touch_day(
                    connection,
                    existing=existing,
                    name=str(alert["name"]),
                    snapshot_id=int(alert["snapshot_id"]),
                    seen_at=seen_at,
                    source_type=str(alert["trigger_type"]),
                    formal_seen=bool(alert["is_formal"]),
                    supplement_seen=bool(alert["is_supplement"]),
                )
            activated = int(
                connection.execute(
                    "SELECT COUNT(*) FROM candidate_repeat_states WHERE active = 1"
                ).fetchone()[0]
            )
            occurrences = int(
                connection.execute("SELECT COUNT(*) FROM candidate_repeat_days").fetchone()[0]
            )
        return RepeatBackfillReport(
            snapshots=snapshots,
            occurrences=occurrences,
            activated=activated,
            skipped=skipped,
        )

    def _batch_is_countable(self, batch: CandidateBatch, seen_at: datetime) -> bool:
        if batch.health is not HealthState.HEALTHY:
            return False
        if len(batch.candidates) != 3:
            return False
        try:
            shanghai_trade_date(seen_at)
            shanghai_trade_date(batch.source_ts)
        except ValueError:
            return False
        first = batch.candidates[0]
        return provider_is_countable(first.provider_version)

    def _observe_candidate(
        self,
        connection: sqlite3.Connection,
        *,
        candidate: Candidate,
        trade_date: date,
        snapshot_id: int,
        seen_at: datetime,
        source_type: str,
    ) -> RepeatProjection:
        if not is_valid_code(candidate.code):
            return RepeatProjection()
        existing = self._load_day(connection, candidate.code, trade_date)
        previous_state = self._load_state_row(connection, candidate.code)
        if existing is not None:
            self._touch_day(
                connection,
                existing=existing,
                name=candidate.name,
                snapshot_id=snapshot_id,
                seen_at=seen_at,
                source_type=source_type,
                formal_seen=candidate.is_formal,
                supplement_seen=candidate.is_supplement,
            )
            return self._load_projection(connection, candidate.code)

        prior_dates = self._load_dates(connection, candidate.code)
        computed = compute_repeat_state(
            (*prior_dates, trade_date),
            previously_active=bool(previous_state and previous_state["active"]),
            sequence_started_on=(
                date.fromisoformat(str(previous_state["sequence_started_on"]))
                if previous_state and previous_state["sequence_started_on"]
                else None
            ),
        )
        seen_iso = seen_at.isoformat()
        activated_at = None
        activated_trade_date = None
        if previous_state and previous_state["active"]:
            activated_at = previous_state["activated_at"]
            activated_trade_date = previous_state["activated_trade_date"]
        elif computed.newly_activated:
            activated_at = seen_iso
            activated_trade_date = trade_date.isoformat()
        connection.execute(
            "INSERT INTO candidate_repeat_days ("
            "code, name, trade_date, first_seen_at, last_seen_at, "
            "first_snapshot_id, last_snapshot_id, source_types_json, "
            "formal_seen, supplement_seen, count_after, span_days_after, "
            "active_after, created_at, updated_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                candidate.code,
                candidate.name,
                trade_date.isoformat(),
                seen_iso,
                seen_iso,
                snapshot_id,
                snapshot_id,
                json.dumps([source_type], ensure_ascii=False),
                int(candidate.is_formal),
                int(candidate.is_supplement),
                computed.occurrence_count,
                computed.span_days,
                int(computed.active),
                seen_iso,
                seen_iso,
            ),
        )
        connection.execute(
            "INSERT INTO candidate_repeat_states ("
            "code, name, active, window_started_on, window_expires_on, "
            "sequence_started_on, occurrence_count, span_days, activated_at, "
            "activated_trade_date, last_seen_on, last_seen_at, last_snapshot_id, "
            "updated_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(code) DO UPDATE SET "
            "name = excluded.name, "
            "active = excluded.active, "
            "window_started_on = excluded.window_started_on, "
            "window_expires_on = excluded.window_expires_on, "
            "sequence_started_on = excluded.sequence_started_on, "
            "occurrence_count = excluded.occurrence_count, "
            "span_days = excluded.span_days, "
            "activated_at = excluded.activated_at, "
            "activated_trade_date = excluded.activated_trade_date, "
            "last_seen_on = excluded.last_seen_on, "
            "last_seen_at = excluded.last_seen_at, "
            "last_snapshot_id = excluded.last_snapshot_id, "
            "updated_at = excluded.updated_at",
            (
                candidate.code,
                candidate.name,
                int(computed.active),
                computed.window_started_on.isoformat() if computed.window_started_on else None,
                computed.window_expires_on.isoformat() if computed.window_expires_on else None,
                computed.sequence_started_on.isoformat() if computed.sequence_started_on else None,
                computed.occurrence_count,
                computed.span_days,
                activated_at,
                activated_trade_date,
                trade_date.isoformat(),
                seen_iso,
                snapshot_id,
                seen_iso,
            ),
        )
        return RepeatProjection(
            active=computed.active,
            occurrence_count=computed.occurrence_count,
            span_days=computed.span_days,
            sequence_started_on=computed.sequence_started_on,
            activated_at=activated_at,
            last_seen_on=trade_date,
        )

    def _touch_day(
        self,
        connection: sqlite3.Connection,
        *,
        existing: Mapping[str, Any],
        name: str,
        snapshot_id: int,
        seen_at: datetime,
        source_type: str,
        formal_seen: bool,
        supplement_seen: bool,
    ) -> None:
        sources = _merge_sources(existing["source_types_json"], source_type)
        connection.execute(
            "UPDATE candidate_repeat_days SET "
            "name = ?, last_seen_at = ?, last_snapshot_id = ?, "
            "source_types_json = ?, formal_seen = ?, supplement_seen = ?, "
            "updated_at = ? WHERE id = ?",
            (
                name,
                seen_at.isoformat(),
                snapshot_id,
                json.dumps(sources, ensure_ascii=False),
                int(bool(existing["formal_seen"]) or formal_seen),
                int(bool(existing["supplement_seen"]) or supplement_seen),
                seen_at.isoformat(),
                int(existing["id"]),
            ),
        )
        connection.execute(
            "UPDATE candidate_repeat_states SET "
            "name = ?, last_seen_at = ?, last_snapshot_id = ?, updated_at = ? "
            "WHERE code = ?",
            (
                name,
                seen_at.isoformat(),
                snapshot_id,
                seen_at.isoformat(),
                str(existing["code"]),
            ),
        )

    def _load_projection(
        self,
        connection: sqlite3.Connection,
        code: str,
    ) -> RepeatProjection:
        row = self._load_state_row(connection, code)
        if row is None:
            return RepeatProjection()
        sequence = (
            date.fromisoformat(str(row["sequence_started_on"]))
            if row["sequence_started_on"]
            else None
        )
        last_seen = (
            date.fromisoformat(str(row["last_seen_on"])) if row["last_seen_on"] else None
        )
        return RepeatProjection(
            active=bool(row["active"]),
            occurrence_count=int(row["occurrence_count"]),
            span_days=int(row["span_days"]),
            sequence_started_on=sequence,
            activated_at=row["activated_at"],
            last_seen_on=last_seen,
        )

    @staticmethod
    def _load_state_row(
        connection: sqlite3.Connection,
        code: str,
    ) -> dict[str, Any] | None:
        row = connection.execute(
            "SELECT code, name, active, window_started_on, window_expires_on, "
            "sequence_started_on, occurrence_count, span_days, activated_at, "
            "activated_trade_date, last_seen_on, last_seen_at, last_snapshot_id "
            "FROM candidate_repeat_states WHERE code = ?",
            (code,),
        ).fetchone()
        if row is None:
            return None
        keys = (
            "code",
            "name",
            "active",
            "window_started_on",
            "window_expires_on",
            "sequence_started_on",
            "occurrence_count",
            "span_days",
            "activated_at",
            "activated_trade_date",
            "last_seen_on",
            "last_seen_at",
            "last_snapshot_id",
        )
        return dict(zip(keys, row))

    @staticmethod
    def _load_day(
        connection: sqlite3.Connection,
        code: str,
        trade_date: date,
    ) -> dict[str, Any] | None:
        row = connection.execute(
            "SELECT id, code, name, trade_date, first_seen_at, last_seen_at, "
            "first_snapshot_id, last_snapshot_id, source_types_json, "
            "formal_seen, supplement_seen, count_after, span_days_after, "
            "active_after FROM candidate_repeat_days "
            "WHERE code = ? AND trade_date = ?",
            (code, trade_date.isoformat()),
        ).fetchone()
        if row is None:
            return None
        keys = (
            "id",
            "code",
            "name",
            "trade_date",
            "first_seen_at",
            "last_seen_at",
            "first_snapshot_id",
            "last_snapshot_id",
            "source_types_json",
            "formal_seen",
            "supplement_seen",
            "count_after",
            "span_days_after",
            "active_after",
        )
        return dict(zip(keys, row))

    @staticmethod
    def _load_dates(connection: sqlite3.Connection, code: str) -> tuple[date, ...]:
        rows = connection.execute(
            "SELECT trade_date FROM candidate_repeat_days WHERE code = ? "
            "ORDER BY trade_date",
            (code,),
        ).fetchall()
        return tuple(date.fromisoformat(str(row[0])) for row in rows)

    @staticmethod
    def _load_backfill_rows(connection: sqlite3.Connection) -> list[dict[str, Any]]:
        rows = connection.execute(
            "SELECT s.id, s.source_ts, s.generated_at, s.health, s.provider_version, "
            "i.rank, i.code, i.name, i.level, i.is_formal, i.is_supplement, "
            "i.price, i.change_pct, i.sector_code, i.sector_name "
            "FROM candidate_snapshots s "
            "JOIN candidate_items i ON i.snapshot_id = s.id "
            "WHERE s.health = ? "
            "ORDER BY s.source_ts ASC, s.id ASC, i.rank ASC",
            (HealthState.HEALTHY.value,),
        ).fetchall()
        keys = (
            "snapshot_id",
            "source_ts",
            "generated_at",
            "health",
            "provider_version",
            "rank",
            "code",
            "name",
            "level",
            "is_formal",
            "is_supplement",
            "price",
            "change_pct",
            "sector_code",
            "sector_name",
        )
        return [dict(zip(keys, row)) for row in rows]

    @staticmethod
    def _load_backfill_alerts(connection: sqlite3.Connection) -> list[dict[str, Any]]:
        rows = connection.execute(
            "SELECT e.snapshot_id, e.displayed_at, e.trigger_type, "
            "i.code, i.name, i.is_formal, i.is_supplement "
            "FROM alert_events e "
            "JOIN candidate_items i ON i.snapshot_id = e.snapshot_id "
            "ORDER BY e.displayed_at ASC, e.id ASC, i.rank ASC"
        ).fetchall()
        keys = (
            "snapshot_id",
            "displayed_at",
            "trigger_type",
            "code",
            "name",
            "is_formal",
            "is_supplement",
        )
        return [dict(zip(keys, row)) for row in rows]


def _merge_sources(raw: object, source_type: str) -> list[str]:
    try:
        parsed = json.loads(str(raw or "[]"))
    except json.JSONDecodeError:
        parsed = []
    sources = [str(item) for item in parsed] if isinstance(parsed, list) else []
    if source_type not in sources:
        sources.append(source_type)
    return sources


def _backfill_candidate(item: Mapping[str, Any], seen_at: datetime) -> Candidate:
    return Candidate(
        code=str(item["code"]),
        name=str(item["name"]),
        sector=str(item.get("sector_name") or ""),
        level=str(item.get("level") or "近"),
        score=0.0,
        price_score=0.0,
        sector_score=0.0,
        trend_score=0.0,
        penalty=0.0,
        reasons=(),
        source_ts=seen_at,
        provider_version="backfill",
        config_version="backfill",
        app_version="backfill",
        price=float(item.get("price") or 0.0),
        change_pct=float(item.get("change_pct") or 0.0),
        sector_code=str(item.get("sector_code") or ""),
        is_formal=bool(item["is_formal"]),
        is_supplement=bool(item["is_supplement"]),
    )
