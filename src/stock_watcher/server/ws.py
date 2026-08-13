"""WebSocket event pump.

One connection per browser; the pump reads the durable outbox by
``after_id``, replays missed events, detects expired cursors and enforces a
bounded per-client queue (slow clients are coalesced then dropped with 1013).
WebSocket is a notification channel, never the source of truth.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from stock_watcher.services import EventOutbox
from stock_watcher.services.public_state import PublicStateBuilder
from stock_watcher.storage import SQLiteStore

HEARTBEAT_SECONDS = 20
SEND_TIMEOUT_SECONDS = 5.0
QUEUE_HARD_LIMIT = 1000
QUEUE_COALESCE_AFTER = 300


def _envelope(
    event_id: int,
    event_type: str,
    occurred_at: str,
    source_commit: str,
    payload: dict[str, Any],
    correlation_id: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "event_id": event_id,
        "event_type": event_type,
        "occurred_at": occurred_at,
        "source_commit": source_commit,
        "correlation_id": correlation_id,
        "payload": payload,
    }


class WebSocketManager:
    def __init__(
        self,
        store: SQLiteStore,
        outbox: EventOutbox,
        public_state: PublicStateBuilder,
        *,
        source_commit: str,
        heartbeat_seconds: int = HEARTBEAT_SECONDS,
    ) -> None:
        self.store = store
        self.outbox = outbox
        self.public_state = public_state
        self.source_commit = source_commit
        self.heartbeat_seconds = heartbeat_seconds
        self._connections: set[WebSocket] = set()

    @property
    def connection_count(self) -> int:
        return len(self._connections)

    async def handle(self, websocket: WebSocket, session: dict[str, Any]) -> None:
        await websocket.accept()
        self._connections.add(websocket)
        try:
            await self._serve(websocket, session)
        finally:
            self._connections.discard(websocket)

    async def _serve(self, websocket: WebSocket, session: dict[str, Any]) -> None:
        minimum = self.outbox.minimum_available_id()
        latest = self.outbox.latest_id()
        requested_after_id = websocket.query_params.get("after_id")
        # A fresh page load starts at the current server watermark. Explicit
        # cursors still replay missed events, including after_id=0 for tests and
        # diagnostics. This keeps browser reopen/refresh from presenting the
        # durable 30-day history as new pop-up alerts.
        after_id = latest
        cursor_issue: str | None = None
        if requested_after_id not in {None, ""}:
            try:
                after_id = int(requested_after_id)
            except ValueError:
                cursor_issue = "cursor_invalid"
            else:
                if after_id < 0:
                    cursor_issue = "cursor_invalid"
                elif after_id > latest:
                    # A browser tab can survive a database restore whose event
                    # ids move backwards. Waiting on the old high watermark
                    # would otherwise suppress every new event until ids catch
                    # up again.
                    cursor_issue = "cursor_ahead"
                elif after_id > 0 and (after_id + 1) < minimum:
                    cursor_issue = "cursor_expired"
        hello = _envelope(
            0,
            "server.hello",
            __import__("datetime").datetime.now(
                __import__("stock_watcher.domain", fromlist=["SHANGHAI"]).SHANGHAI
            ).isoformat(),
            self.source_commit,
            {
                "business_timezone": "Asia/Shanghai",
                "heartbeat_seconds": self.heartbeat_seconds,
                "latest_event_id": latest,
                "user_role": str(session.get("role")),
            },
        )
        await _send(websocket, hello)
        if cursor_issue is not None:
            await _send(
                websocket,
                _envelope(
                    0,
                    "server.resync_required",
                    _now_iso(),
                    self.source_commit,
                    {
                        "reason": cursor_issue,
                        "minimum_event_id": minimum,
                        "latest_event_id": latest,
                    },
                ),
            )
            return
        state = self.public_state.build(
            now=__import__("datetime").datetime.now(
                __import__("stock_watcher.domain", fromlist=["SHANGHAI"]).SHANGHAI
            )
        )
        await _send(
            websocket,
            _envelope(
                    0,
                    "state.snapshot",
                    _now_iso(),
                    self.source_commit,
                    {
                        "state_version": int(state.get("state_version", 0)),
                        "service_state": state.get("service_state", "starting"),
                        "market_state": state.get("market_state", "unknown"),
                        "snapshot_id": state.get("snapshot_id"),
                        "candidates": state.get("candidates", []),
                        "overall_weak": bool(state.get("overall_weak", False)),
                        "source_ts": state.get("source_ts"),
                    },
                ),
        )
        cursor = after_id
        pending: list[dict[str, Any]] = []
        last_send = __import__("time").monotonic()
        while True:
            try:
                now = __import__("time").monotonic()
                if now - last_send >= self.heartbeat_seconds:
                    await _send(
                        websocket,
                        _envelope(
                                0,
                                "server.heartbeat",
                                _now_iso(),
                                self.source_commit,
                                {"server_time": _now_iso()},
                            ),
                    )
                    last_send = now
                events = self.outbox.read_since(cursor, limit=100)
                for event in events:
                    cursor = int(event["event_id"])
                    if _event_visible(event, session):
                        pending.append(event)
                if events and (not pending or int(pending[-1]["event_id"]) < cursor):
                    pending.append(
                        {
                            "event_id": cursor,
                            "event_type": "server.cursor",
                            "occurred_at": _now_iso(),
                            "source_commit": self.source_commit,
                            "correlation_id": None,
                            "payload": {"last_event_id": cursor},
                        }
                    )
                if len(pending) > QUEUE_COALESCE_AFTER:
                    pending = _coalesce(pending, self.source_commit)
                if len(pending) > QUEUE_HARD_LIMIT:
                    await websocket.close(code=1013, reason="client too slow")
                    return
                while pending:
                    event = pending.pop(0)
                    await _send(
                        websocket,
                        _envelope(
                                int(event["event_id"]),
                                str(event["event_type"]),
                                str(event["occurred_at"]),
                                str(event["source_commit"]),
                                event["payload"],
                                event.get("correlation_id"),
                            ),
                    )
                await asyncio.sleep(1.0)
            except WebSocketDisconnect:
                return
            except RuntimeError:
                return


def _coalesce(pending: list[dict[str, Any]], source_commit: str) -> list[dict[str, Any]]:
    """Merge consecutive state events while preserving the newest snapshot."""
    merged: list[dict[str, Any]] = []
    latest_state: dict[str, Any] | None = None
    for event in pending:
        if event["event_type"] in {"state.snapshot", "state.changed"}:
            latest_state = event
        else:
            merged.append(event)
    if latest_state is not None:
        merged.append(latest_state)
    return merged


def _event_visible(event: dict[str, Any], session: dict[str, Any]) -> bool:
    role = str(session.get("role", ""))
    visibility = str(event.get("visibility", "all"))
    if visibility == "admin" and role != "admin":
        return False
    if visibility == "tester" and role not in {"tester", "admin"}:
        return False
    if event.get("event_type") == "command.updated" and role != "admin":
        payload = event.get("payload")
        if not isinstance(payload, dict):
            return False
        requested_by = payload.get("requested_by")
        user_id = session.get("user_id")
        if not isinstance(requested_by, int) or not isinstance(user_id, int):
            return False
        try:
            return requested_by == user_id
        except (TypeError, ValueError):
            return False
    return True


async def _send(websocket: WebSocket, payload: dict[str, Any]) -> None:
    try:
        await asyncio.wait_for(
            websocket.send_text(json.dumps(payload, ensure_ascii=False)),
            timeout=SEND_TIMEOUT_SECONDS,
        )
    except TimeoutError as error:
        try:
            await websocket.close(code=1013, reason="client too slow")
        finally:
            raise RuntimeError("websocket send timed out") from error


def _now_iso() -> str:
    from datetime import datetime

    from stock_watcher.domain import SHANGHAI

    return datetime.now(SHANGHAI).isoformat()
