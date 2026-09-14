"""User-owned approvals with optimistic concurrency and durable idempotency.

No provider, worker, ranking, notification or external-network dependencies.
No unselected candidate is manufactured into a negative feedback example.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

from .schema import check_core, check_extension, database

SHANGHAI = ZoneInfo("Asia/Shanghai")
MAX_SQLITE_INT = (1 << 63) - 1
CODE = re.compile(r"[0-9]{6}(?:\.(?:SH|SZ|BJ))?\Z")
REQUEST_ID = re.compile(r"[A-Za-z0-9_-]{16,96}\Z")
FACTOR_FIELDS = (
    "score", "price_score", "sector_score", "trend_score", "penalty", "core_score",
    "fund_score", "total_score", "data_completeness", "velocity_pct",
    "acceleration_pct", "velocity_available", "velocity_1m_pct", "velocity_3m_pct",
    "velocity_5m_pct", "volume_ratio_1m", "amount_ratio_1m", "sector_gate_passed",
    "sector_up_ratio", "sector_strong_count", "sector_rank_percentile",
    "sector_median_change_pct", "sector_rank", "sector_valid_count",
)
LABEL_FIELDS = (
    "sector_type", "super_large_state", "large_state", "fund_sync_state", "trend_label",
)


class FeedbackError(RuntimeError):
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code, self.status = code, status


@dataclass(frozen=True)
class ApprovalCommand:
    snapshot_id: int
    code: str
    selected: bool
    request_id: str
    expected_version: int
    surface: str = "dashboard"

    def validate(self) -> None:
        if type(self.snapshot_id) is not int or not 0 < self.snapshot_id <= MAX_SQLITE_INT:
            raise FeedbackError("invalid_request", "候选快照无效")
        if not isinstance(self.code, str) or not CODE.fullmatch(self.code):
            raise FeedbackError("invalid_request", "股票代码无效")
        if type(self.selected) is not bool:
            raise FeedbackError("invalid_request", "认可状态必须是布尔值")
        if (
            type(self.expected_version) is not int
            or not 0 <= self.expected_version < MAX_SQLITE_INT
        ):
            raise FeedbackError("invalid_request", "反馈版本无效")
        if not isinstance(self.request_id, str) or not REQUEST_ID.fullmatch(self.request_id):
            raise FeedbackError("invalid_request", "请求标识无效")
        if self.surface not in {"dashboard", "history"}:
            raise FeedbackError("invalid_request", "反馈入口无效")


def utc_now() -> datetime:
    return datetime.now(UTC)


def json_text(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def source_time(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value)
    except (ValueError, TypeError) as error:
        raise FeedbackError("invalid_snapshot", "候选源时间不可验证", 422) from error
    if result.tzinfo is None:
        raise FeedbackError("invalid_snapshot", "候选源时间缺少时区", 422)
    return result


def _active_user(connection: sqlite3.Connection, user_id: int) -> None:
    if type(user_id) is not int or user_id <= 0:
        raise FeedbackError("unauthorized", "登录已失效，请重新登录", 401)
    row = connection.execute("SELECT active FROM web_users WHERE user_id=?", (user_id,)).fetchone()
    if row is None or not row[0]:
        raise FeedbackError("unauthorized", "登录已失效，请重新登录", 401)


def _state(connection: sqlite3.Connection, user_id: int, day: str, code: str) -> dict[str, Any]:
    row = connection.execute(
        "SELECT selected, version, first_selected_at, updated_at, last_event_id "
        "FROM web_candidate_approvals WHERE user_id=? AND trade_date=? AND code=?",
        (user_id, day, code),
    ).fetchone()
    return {
        "trade_date": day, "code": code, "selected": bool(row[0]) if row else False,
        "version": int(row[1]) if row else 0,
        "feedback_status": ("approved" if row[0] else "withdrawn") if row else "unreviewed",
        "first_selected_at": row[2] if row else None,
        "updated_at": row[3] if row else None,
        "last_event_id": row[4] if row else None,
    }


def _snapshot(connection: sqlite3.Connection, snapshot_id: int) -> sqlite3.Row:
    row = connection.execute(
        "SELECT id, source_ts, generated_at, health, overall_weak, provider_version, "
        "config_version, app_version FROM candidate_snapshots WHERE id=?", (snapshot_id,),
    ).fetchone()
    if row is None:
        raise FeedbackError("snapshot_not_found", "快照已不可用，请刷新候选后重试", 404)
    source_time(row["source_ts"])
    return cast(sqlite3.Row, row)


def _candidate(row: sqlite3.Row) -> dict[str, Any]:
    """Allowlisted context only, never copies an arbitrary database JSON blob."""
    item = {key: row[key] for key in (
        "rank", "code", "name", "price", "change_pct", "level", "sector_name", "fund_label",
    )}
    for key in ("price", "change_pct"):
        try:
            value = float(item[key])
        except (TypeError, ValueError, OverflowError) as error:
            raise FeedbackError("invalid_snapshot", "候选报价不可验证", 422) from error
        if not math.isfinite(value) or (key == "price" and value <= 0):
            raise FeedbackError("invalid_snapshot", "候选报价不可验证", 422)
        item[key] = value
    if int(item["rank"]) not in {1, 2, 3}:
        raise FeedbackError("invalid_snapshot", "候选排名不可验证", 422)
    raw: dict[str, Any] = {}
    try:
        parsed = json.loads(row["payload_json"] or "{}")
        if isinstance(parsed, dict):
            raw = parsed
    except (ValueError, TypeError):
        pass
    factors: dict[str, Any] = {}
    for key in FACTOR_FIELDS:
        factor = raw.get(key)
        if isinstance(factor, bool) or factor is None:
            factors[key] = factor
        elif type(factor) in (int, float) and math.isfinite(factor):
            factors[key] = factor
    for key in LABEL_FIELDS:
        label = raw.get(key)
        if isinstance(label, str):
            factors[key] = label[:100]
    # No free-text notes, user identifiers, secret-like fields or arbitrary JSON.
    item["factors"] = factors
    item["factor_context_available"] = any(value is not None for value in factors.values())
    return item


class ApprovalRepository:
    def __init__(self, path: Path, *, clock: Callable[[], datetime] = utc_now) -> None:
        self.path, self.clock = Path(path), clock

    def snapshot_state(self, user_id: int, snapshot_id: int) -> dict[str, Any]:
        if type(snapshot_id) is not int or not 0 < snapshot_id <= MAX_SQLITE_INT:
            raise FeedbackError("invalid_request", "候选快照无效")
        with database(self.path) as connection:
            check_core(connection)
            check_extension(connection)
            connection.execute("BEGIN")
            _active_user(connection, user_id)
            snapshot = _snapshot(connection, snapshot_id)
            day = source_time(snapshot["source_ts"]).astimezone(SHANGHAI).date().isoformat()
            rows = connection.execute(
                "SELECT code FROM candidate_items WHERE snapshot_id=? AND rank BETWEEN 1 AND 3 "
                "ORDER BY rank", (snapshot_id,),
            ).fetchall()
            codes = [str(row[0]) for row in rows]
            if len(codes) > 3 or len(codes) != len(set(codes)):
                raise FeedbackError("invalid_snapshot", "候选快照内容不一致", 422)
            return {
                "user_id": user_id, "snapshot_id": snapshot_id, "trade_date": day,
                "source_ts": snapshot["source_ts"],
                "items": [_state(connection, user_id, day, code) for code in codes],
            }

    def apply(self, user_id: int, command: ApprovalCommand) -> dict[str, Any]:
        command.validate()
        moment = self.clock()
        if moment.tzinfo is None:
            raise ValueError("feedback clock must be timezone-aware")
        moment = moment.astimezone(UTC)
        now = moment.isoformat()
        digest = hashlib.sha256(json_text(asdict(command)).encode()).hexdigest()
        with database(self.path, write=True) as connection:
            check_core(connection)
            check_extension(connection)
            connection.execute("BEGIN IMMEDIATE")
            try:
                _active_user(connection, user_id)
                previous = connection.execute(
                    "SELECT request_hash, trade_date, code, receipt_json "
                    "FROM web_candidate_approval_requests WHERE user_id=? AND request_id=?",
                    (user_id, command.request_id),
                ).fetchone()
                if previous is not None:
                    if previous["request_hash"] != digest:
                        raise FeedbackError("idempotency_conflict", "请求标识已用于其他操作", 409)
                    # Even after a later revoke or source retention cleanup, return
                    # the original receipt AND the CURRENT personal state.
                    result = {
                        "user_id": user_id, "replayed": True,
                        "receipt": json.loads(previous["receipt_json"]),
                        "state": _state(
                            connection, user_id, previous["trade_date"], previous["code"]
                        ),
                    }
                    connection.commit()
                    return result
                count = connection.execute(
                    "SELECT COUNT(*) FROM web_candidate_approval_requests "
                    "WHERE user_id=? AND created_at>=?",
                    (user_id, (moment - timedelta(minutes=1)).isoformat()),
                ).fetchone()[0]
                if count >= 60:
                    raise FeedbackError("rate_limited", "操作过于频繁，请稍后重试", 429)
                snapshot = _snapshot(connection, command.snapshot_id)
                source = source_time(snapshot["source_ts"])
                if source > moment + timedelta(seconds=60):
                    raise FeedbackError("invalid_snapshot", "候选源时间异常，请核对数据状态", 422)
                day = source.astimezone(SHANGHAI).date().isoformat()
                rows = connection.execute(
                    "SELECT rank, code, name, price, change_pct, level, sector_name, fund_label, "
                    "payload_json FROM candidate_items WHERE snapshot_id=? ORDER BY rank",
                    (command.snapshot_id,),
                ).fetchall()
                peers = [_candidate(row) for row in rows]
                if not 1 <= len(peers) <= 3 or len({p["code"] for p in peers}) != len(peers):
                    raise FeedbackError("invalid_snapshot", "候选快照内容不一致", 422)
                chosen = next((item for item in peers if item["code"] == command.code), None)
                if chosen is None:
                    raise FeedbackError("candidate_not_found", "该股票不在指定候选快照中", 404)
                before = _state(connection, user_id, day, command.code)
                if before["version"] != command.expected_version:
                    raise FeedbackError("version_conflict", "其他页面已更新反馈，请核对后重选", 409)
                changed = before["selected"] != command.selected
                event_id = None
                version = before["version"]
                if changed:
                    version += 1
                    public = connection.execute(
                        "SELECT snapshot_id FROM web_public_state WHERE state_key='current'"
                    ).fetchone()
                    is_current = bool(public and public[0] == command.snapshot_id)
                    context = {
                        "context_version": 1,
                        "snapshot": dict(snapshot),
                        "candidate": chosen, "peer_candidates": peers,
                        "snapshot_is_current_at_acceptance": is_current,
                        "candidate_age_seconds": round(
                            max(0, (moment - source).total_seconds()), 3
                        ),
                        "source_trade_date": day,
                        "recorded_trade_date": moment.astimezone(SHANGHAI).date().isoformat(),
                        "client_surface": command.surface,
                        "client_surface_is_untrusted_metadata": True,
                        "retrospective": command.surface == "history" or not is_current
                        or day != moment.astimezone(SHANGHAI).date().isoformat(),
                        "label_semantics": "subjective_approval_not_trade_or_return",
                    }
                    action = "approve" if command.selected else "revoke"
                    cursor = connection.execute(
                        "INSERT INTO web_candidate_approval_events "
                        "(user_id, trade_date, code, snapshot_id, action, recorded_at, version, "
                        "request_id, context_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (user_id, day, command.code, command.snapshot_id, action, now, version,
                         command.request_id, json_text(context)),
                    )
                    event_id = cursor.lastrowid
                    connection.execute(
                        "INSERT INTO web_candidate_approvals "
                        "(user_id, trade_date, code, selected, version, "
                        "first_selected_at, updated_at, "
                        "last_event_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                        "ON CONFLICT(user_id, trade_date, code) DO UPDATE SET "
                        "selected=excluded.selected, version=excluded.version, "
                        "updated_at=excluded.updated_at, last_event_id=excluded.last_event_id",
                        (user_id, day, command.code, int(command.selected), version,
                         before["first_selected_at"] or now, now, event_id),
                    )
                receipt = {
                    "request_id": command.request_id, "changed": changed, "event_id": event_id,
                    "selected": command.selected, "version": version, "recorded_at": now,
                    "snapshot_id": command.snapshot_id, "trade_date": day, "code": command.code,
                }
                connection.execute(
                    "INSERT INTO web_candidate_approval_requests "
                    "(user_id, request_id, request_hash, trade_date, code, "
                    "created_at, receipt_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (user_id, command.request_id, digest, day, command.code, now,
                     json_text(receipt)),
                )
                result = {
                    "user_id": user_id, "replayed": False, "receipt": receipt,
                    "state": _state(connection, user_id, day, command.code),
                }
                connection.commit()
                return result
            except BaseException:
                connection.rollback()
                raise

    def events(
        self, user_id: int, *, cursor: int | None = None, limit: int = 50,
        from_date: date | None = None, to_date: date | None = None, admin: bool = False,
    ) -> dict[str, Any]:
        if not 1 <= limit <= 100 or (cursor is not None and cursor <= 0):
            raise FeedbackError("invalid_request", "分页参数无效")
        if from_date and to_date and from_date > to_date:
            raise FeedbackError("invalid_request", "日期范围无效")
        with database(self.path) as connection:
            check_core(connection)
            check_extension(connection)
            _active_user(connection, user_id)
            if admin:
                role = connection.execute(
                    "SELECT role FROM web_users WHERE user_id=?", (user_id,)
                ).fetchone()
                if role is None or role[0] != "admin":
                    raise FeedbackError("forbidden", "权限不足", 403)
            clauses: list[str] = []
            values: list[Any] = []
            if not admin:
                clauses.append("user_id=?")
                values.append(user_id)
            if cursor is not None:
                clauses.append("event_id<?")
                values.append(cursor)
            if from_date:
                clauses.append("trade_date>=?")
                values.append(from_date.isoformat())
            if to_date:
                clauses.append("trade_date<=?")
                values.append(to_date.isoformat())
            where = " WHERE " + " AND ".join(clauses) if clauses else ""
            rows = connection.execute(
                "SELECT event_id, user_id, trade_date, code, snapshot_id, action, recorded_at, "
                "version, context_json FROM web_candidate_approval_events" + where
                + " ORDER BY event_id DESC LIMIT ?", (*values, limit + 1),
            ).fetchall()
            items = []
            for row in rows[:limit]:
                item = dict(row)
                item["context"] = json.loads(item.pop("context_json"))
                items.append(item)
            return {"user_id": user_id, "items": items,
                    "next_cursor": items[-1]["event_id"] if len(rows) > limit else None}
