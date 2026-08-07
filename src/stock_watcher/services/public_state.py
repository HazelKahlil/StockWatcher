"""Shared public state projection for REST and WebSocket clients.

All users see exactly one business state; permissions only control visibility
and management actions, never a per-user scan.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from stock_watcher.storage import SQLiteStore

SERVICE_STATE_ALIASES = {
    "WARMING": "warming",
    "HEALTHY": "healthy",
    "STALE": "stale",
    "STOPPED": "stopped",
    "starting": "starting",
    "warming": "warming",
    "healthy": "healthy",
    "stale": "stale",
    "stopped": "stopped",
}


def normalize_service_state(value: object) -> str:
    """Map the baseline HealthState enum (uppercase) to the WS/REST contract
    lowercase service-state vocabulary."""
    return SERVICE_STATE_ALIASES.get(str(value), str(value) or "starting")


def _parsed_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


class PublicStateBuilder:
    """Read-only projection over SQLite + the worker lease row."""

    def __init__(self, store: SQLiteStore) -> None:
        self.store = store

    def build(
        self,
        *,
        now: datetime,
        worker_lease: dict[str, object] | None = None,
        worker_running: bool | None = None,
    ) -> dict[str, Any]:
        public = self.store.read_public_state()
        stored: dict[str, Any] = {}
        if public is None:
            payload: dict[str, Any] = {
                "state_version": 0,
                "service_state": "starting",
                "market_state": "unknown",
                "snapshot_id": None,
                "candidates": [],
                "overall_weak": False,
                "source_ts": None,
            }
        else:
            stored = json.loads(str(public["payload_json"]))
            payload = {
                "state_version": int(public["state_version"]),
                "service_state": normalize_service_state(
                    stored.get("service_state", "starting")
                ),
                "market_state": stored.get("market_state", "unknown"),
                "snapshot_id": public["snapshot_id"],
                "candidates": stored.get("candidates", []),
                "overall_weak": bool(stored.get("overall_weak", False)),
                "source_ts": public["source_ts"],
            }
        payload["updated_at"] = public["updated_at"] if public else None
        payload["worker_heartbeat_age_seconds"] = self._worker_heartbeat_age(
            worker_lease, now=now
        )
        payload["worker_running"] = bool(worker_running)
        payload["business_timezone"] = "Asia/Shanghai"
        payload["last_scan"] = self._last_scan()
        payload["tasks"] = self._today_tasks(now)
        payload["last_alert"] = self._last_alert()
        payload["last_summary"] = self._last_summary()
        payload["active_command"] = self._active_command()
        payload["fund_module"] = stored.get("fund_module", "unavailable")
        payload["formal_count"] = stored.get("formal_count", 0)
        return payload

    def _worker_heartbeat_age(
        self,
        worker_lease: dict[str, object] | None,
        *,
        now: datetime,
    ) -> float | None:
        if worker_lease is None or not worker_lease.get("held"):
            return None
        heartbeat = _parsed_datetime(worker_lease.get("heartbeat_at"))
        if heartbeat is None:
            return None
        return max(0.0, (now - heartbeat).total_seconds())

    def _last_scan(self) -> dict[str, Any] | None:
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT id, started_at, completed_at, trigger_type, health, "
                "source_ts, coverage_ratio, elapsed_seconds, source_age_seconds, detail "
                "FROM scan_runs ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        keys = (
            "id",
            "started_at",
            "completed_at",
            "trigger_type",
            "health",
            "source_ts",
            "coverage_ratio",
            "elapsed_seconds",
            "source_age_seconds",
            "detail",
        )
        return dict(zip(keys, row))

    def _today_tasks(self, now: datetime) -> list[dict[str, Any]]:
        with self.store.connect() as connection:
            rows = connection.execute(
                "SELECT task_key, task_type, trade_date, target_at, deadline_at, "
                "state, attempts, updated_at, detail, snapshot_id "
                "FROM automation_tasks WHERE trade_date = ? ORDER BY target_at",
                (now.date().isoformat(),),
            ).fetchall()
        keys = (
            "task_key",
            "task_type",
            "trade_date",
            "target_at",
            "deadline_at",
            "state",
            "attempts",
            "updated_at",
            "detail",
            "snapshot_id",
        )
        return [dict(zip(keys, row)) for row in rows]

    def _last_alert(self) -> dict[str, Any] | None:
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT id, snapshot_id, displayed_at, decision, channel, "
                "trigger_type, detail_json FROM alert_events "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        return {
            "alert_id": row[0],
            "snapshot_id": row[1],
            "displayed_at": row[2],
            "decision": row[3],
            "channel": row[4],
            "trigger_type": row[5],
            "detail": json.loads(row[6]),
        }

    def _last_summary(self) -> dict[str, Any] | None:
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT trade_date, generated_at, alert_count, version, catch_up "
                "FROM daily_summaries ORDER BY trade_date DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        return {
            "trade_date": row[0],
            "generated_at": row[1],
            "alert_count": row[2],
            "version": row[3],
            "catch_up": row[4],
        }

    def _active_command(self) -> dict[str, Any] | None:
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT command_id, command_type, status, requested_by, "
                "requested_at, started_at, expires_at, attempts, error_code "
                "FROM web_commands WHERE status IN ('queued', 'running') "
                "ORDER BY requested_at LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        keys = (
            "command_id",
            "command_type",
            "status",
            "requested_by",
            "requested_at",
            "started_at",
            "expires_at",
            "attempts",
            "error_code",
        )
        return dict(zip(keys, row))

    def service_state_market_label(self, now: datetime) -> tuple[str, str]:
        """Map (service state, market session) for the dashboard status bar."""
        public = self.store.read_public_state()
        service_state = "starting"
        if public is not None:
            stored = json.loads(str(public["payload_json"]))
            service_state = str(stored.get("service_state", "starting"))
        current = now.timetz().replace(tzinfo=None)
        from datetime import time

        if current < time(9, 30):
            market = "盘前"
        elif current <= time(11, 30):
            market = "上午盘"
        elif current < time(13, 0):
            market = "午休"
        elif current <= time(15, 0):
            market = "下午盘"
        elif current <= time(15, 30):
            market = "盘后"
        else:
            market = "休市"
        return service_state, market
