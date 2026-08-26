"""REST API routers implementing contracts/openapi.yaml.

All reads go through shared projections; all mutations are durable commands.
The web process never instantiates providers or runs scans.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, cast

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from stock_watcher.domain import SHANGHAI, OutcomeStatus, build_outcome_review
from stock_watcher.runtime import candidate_outcome_rows
from stock_watcher.runtime.repeat_tracker import (
    CandidateRepeatTracker,
    empty_repeat_fields,
    parse_shanghai_timestamp,
)
from stock_watcher.services import CommandService, CommandType, EventOutbox
from stock_watcher.services.public_state import PublicStateBuilder
from stock_watcher.services.secret_service import SecretService
from stock_watcher.storage import SQLiteStore
from stock_watcher.storage.web import (
    AuditLogRepository,
    LastActiveAdminError,
    UserRepository,
)

from .auth import SESSION_COOKIE_NAME, AuthError, AuthService, csrf_value_for_session
from .redaction import redact_value
from .security import request_origin_matches, trusted_client_ip


class LoginPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class UserCreatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=12, max_length=256)
    role: Literal["tester", "admin"] = "tester"


class UserUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    role: Literal["tester", "admin"] | None = None
    active: bool | None = None
    password: str | None = Field(default=None, min_length=12, max_length=256)


class TokenPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    token: str = Field(min_length=1, max_length=512)


def _now() -> datetime:
    return datetime.now(SHANGHAI)


def _parse_date(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise ApiError("invalid date", 400, "invalid_date")


def _outcome_stats_payload(value: Any) -> dict[str, Any]:
    return {
        "total_count": value.total_count,
        "settled_count": value.settled_count,
        "win_count": value.win_count,
        "loss_count": value.loss_count,
        "flat_count": value.flat_count,
        "win_rate": value.win_rate,
        "average_return_pct": value.average_return_pct,
        "median_return_pct": value.median_return_pct,
    }


def _outcome_reason(status: OutcomeStatus, reason: str | None) -> str:
    if status is OutcomeStatus.SETTLED:
        return "已按可验证行情完成复盘"
    if status is OutcomeStatus.PENDING:
        return "等待可验证行情"
    if reason == "historical_minute_suspended_or_no_trade":
        return "目标分钟无成交，未纳入统计"
    if reason == "historical_minute_missing_or_ambiguous":
        return "精确分钟行情不可验证，未纳入统计"
    if reason and reason.startswith("calendar:"):
        return "交易日历暂不可验证，未纳入统计"
    return "缺少可验证行情，未纳入统计"


def _nonnegative_count(value: object) -> int:
    try:
        return max(0, int(str(value or 0)))
    except ValueError:
        return 0


def _backfill_payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {
            "status": "pending",
            "message": "历史回补状态待确认；从新固定提醒开始记录不受影响。",
            "settled": 0,
            "unavailable": 0,
            "skipped": 0,
            "pending": 0,
        }
    status = str(value.get("status") or "pending")
    allowed = {"running", "completed", "partial", "failed"}
    if status not in allowed:
        status = "pending"
    counts = {
        "settled": _nonnegative_count(value.get("settled")),
        "unavailable": _nonnegative_count(value.get("unavailable")),
        "skipped": _nonnegative_count(value.get("skipped")),
        "pending": _nonnegative_count(value.get("pending")),
    }
    messages = {
        "running": "正在检查可验证的固定提醒历史……",
        "completed": "可验证历史已回补；无法验证的数据不计入统计。",
        "partial": (
            f"已回补{counts['settled']}笔，"
            f"{counts['unavailable'] + counts['skipped']}笔因缺少可验证行情未纳入统计。"
            + (f"另有{counts['pending']}笔等待重试。" if counts["pending"] else "")
        ),
        "failed": "历史回补检查失败；从新固定提醒开始记录不受影响。",
        "pending": "历史回补状态待确认；从新固定提醒开始记录不受影响。",
    }
    return {
        "status": status,
        "message": messages[status],
        **counts,
    }


class ApiError(RuntimeError):
    def __init__(self, message: str, status_code: int = 400, code: str = "bad_request") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


def error_response(
    request: Request,
    message: str,
    status_code: int,
    code: str,
    *,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        headers=headers,
        content={
            "error": {
                "code": code,
                "message": redact_value(message),
                "request_id": request.headers.get("X-Request-Id") or str(uuid.uuid4()),
                "details": None,
            }
        },
    )


# -- dependencies ---------------------------------------------------------


def app_state(request: Request) -> dict[str, Any]:
    return cast(dict[str, Any], request.app.state)


def get_store(request: Request) -> SQLiteStore:
    return cast(SQLiteStore, request.app.state.store)


def get_read_store(request: Request) -> SQLiteStore:
    return cast(SQLiteStore, request.app.state.read_store)


def get_auth(request: Request) -> AuthService:
    return cast(AuthService, request.app.state.auth)


def get_commands(request: Request) -> CommandService:
    return cast(CommandService, request.app.state.commands)


def get_outbox(request: Request) -> EventOutbox:
    return cast(EventOutbox, request.app.state.outbox)


def get_public_state(request: Request) -> PublicStateBuilder:
    return cast(PublicStateBuilder, request.app.state.public_state)


def get_secrets(request: Request) -> SecretService | None:
    return cast(SecretService | None, request.app.state.secrets)


def get_audit(request: Request) -> AuditLogRepository:
    return cast(AuditLogRepository, request.app.state.audit)


def get_users(request: Request) -> UserRepository:
    return cast(UserRepository, request.app.state.users)


def current_session(
    request: Request,
    auth: AuthService = Depends(get_auth),
) -> dict[str, Any]:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    session = auth.authenticate(token)
    if session is None:
        raise ApiError("登录已失效，请重新登录", 401, "unauthorized")
    return session


def require_admin(session: dict[str, Any] = Depends(current_session)) -> dict[str, Any]:
    if session.get("role") != "admin":
        raise ApiError("权限不足", 403, "forbidden")
    return session


def require_csrf(
    request: Request,
    session: dict[str, Any] = Depends(current_session),
    auth: AuthService = Depends(get_auth),
) -> dict[str, Any]:
    settings = request.app.state.settings
    if not request_origin_matches(request.headers, str(settings.public_origin)):
        raise ApiError("请求来源校验失败", 403, "origin_mismatch")
    token = request.cookies.get(SESSION_COOKIE_NAME)
    csrf_value = request.headers.get("X-CSRF-Token", "")
    if not auth.require_csrf(token or "", csrf_value):
        raise ApiError("CSRF 校验失败", 403, "csrf_mismatch")
    return session


def require_admin_csrf(
    request: Request,
    session: dict[str, Any] = Depends(require_admin),
    auth: AuthService = Depends(get_auth),
) -> dict[str, Any]:
    settings = request.app.state.settings
    if not request_origin_matches(request.headers, str(settings.public_origin)):
        raise ApiError("请求来源校验失败", 403, "origin_mismatch")
    token = request.cookies.get(SESSION_COOKIE_NAME)
    csrf_value = request.headers.get("X-CSRF-Token", "")
    if not auth.require_csrf(token or "", csrf_value):
        raise ApiError("CSRF 校验失败", 403, "csrf_mismatch")
    return session


def client_ip(request: Request) -> str | None:
    settings = request.app.state.settings
    peer = request.client.host if request.client else None
    return trusted_client_ip(request.headers, peer, settings.trusted_proxy_cidrs)


# -- auth router -----------------------------------------------------------


def auth_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

    @router.post("/login")
    async def login(
        request: Request,
        payload: LoginPayload,
        auth: AuthService = Depends(get_auth),
    ) -> Response:
        settings = request.app.state.settings
        if not request_origin_matches(request.headers, str(settings.public_origin)):
            return error_response(
                request,
                "请求来源校验失败",
                403,
                "origin_mismatch",
            )
        semaphore: asyncio.Semaphore = request.app.state.login_semaphore
        try:
            await asyncio.wait_for(semaphore.acquire(), timeout=0.1)
        except TimeoutError:
            return error_response(
                request,
                "登录校验繁忙，请稍后重试",
                429,
                "login_busy",
                headers={"Retry-After": "1"},
            )
        try:
            result = await asyncio.to_thread(
                auth.login,
                username=payload.username,
                password=payload.password,
                client_ip=client_ip(request),
                user_agent=request.headers.get("User-Agent", ""),
            )
        except AuthError as error:
            headers = (
                {"Retry-After": str(error.retry_after)}
                if error.retry_after is not None
                else None
            )
            return error_response(
                request,
                str(error),
                error.status_code,
                error.code,
                headers=headers,
            )
        finally:
            semaphore.release()
        response = JSONResponse(
            status_code=200,
            content={"user": result["user"]},
        )
        _set_session_cookie(
            response,
            str(result["token"]),
            secure=_cookie_secure(request),
            max_age_seconds=_session_cookie_max_age(request),
        )
        return response

    @router.post("/logout")
    async def logout(
        request: Request,
        session: dict[str, Any] = Depends(require_csrf),
        auth: AuthService = Depends(get_auth),
    ) -> Response:
        token = request.cookies.get(SESSION_COOKIE_NAME)
        await asyncio.to_thread(auth.logout, token or "")
        if session.get("session_token_hash"):
            await request.app.state.ws_manager.disconnect_session(
                str(session["session_token_hash"]),
            )
        response = Response(status_code=204)
        response.delete_cookie(
            SESSION_COOKIE_NAME,
            path="/",
            secure=_cookie_secure(request),
            httponly=True,
            samesite="lax",
        )
        return response

    return router


def _cookie_secure(request: Request) -> bool:
    settings = getattr(request.app.state, "settings", None)
    public_origin = str(getattr(settings, "public_origin", ""))
    return request.url.scheme == "https" or public_origin.lower().startswith("https://")


def _session_cookie_max_age(request: Request) -> int:
    settings = getattr(request.app.state, "settings", None)
    absolute_hours = float(getattr(settings, "session_absolute_hours", 12.0))
    return max(1, int(absolute_hours * 3600))


def _set_session_cookie(
    response: Response,
    token: str,
    *,
    secure: bool,
    max_age_seconds: int,
) -> None:
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        max_age=max_age_seconds,
        path="/",
        secure=secure,
        httponly=True,
        samesite="lax",
    )


# -- state router ----------------------------------------------------------


def state_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["state"])

    @router.get("/me")
    async def me(
        request: Request,
        session: dict[str, Any] = Depends(current_session),
        auth: AuthService = Depends(get_auth),
    ) -> dict[str, Any]:
        token = request.cookies.get(SESSION_COOKIE_NAME) or ""
        csrf = csrf_value_for_session(token)
        user = {
            "user_id": session["user_id"],
            "username": session["username"],
            "role": session["role"],
            "active": bool(session["active"]),
            "created_at": session["created_at"],
            "last_login_at": session.get("last_login_at"),
        }
        return {"user": user, "csrf_token": csrf, "requires_csrf": True}

    @router.get("/state")
    async def state(
        request: Request,
        session: dict[str, Any] = Depends(current_session),
        public_state: PublicStateBuilder = Depends(get_public_state),
        store: SQLiteStore = Depends(get_read_store),
    ) -> dict[str, Any]:
        worker_lease = None
        with store.connect() as connection:
            row = connection.execute(
                "SELECT holder_id, source_commit, acquired_at, heartbeat_at, "
                "expires_at, fencing_token FROM service_leases "
                "WHERE lease_name = 'stockwatcher-worker'"
            ).fetchone()
            if row is not None:
                worker_lease = {
                    "holder_id": row[0],
                    "source_commit": row[1],
                    "acquired_at": row[2],
                    "heartbeat_at": row[3],
                    "expires_at": row[4],
                    "fencing_token": row[5],
                    "held": True,
                }
        return public_state.build(now=_now(), worker_lease=worker_lease, worker_running=True)

    @router.get("/candidates/current")
    async def candidates_current(
        request: Request,
        session: dict[str, Any] = Depends(current_session),
        public_state: PublicStateBuilder = Depends(get_public_state),
    ) -> dict[str, Any]:
        return public_state.build(now=_now())

    @router.get("/candidates/{code}")
    async def candidate_detail(
        code: str,
        snapshot_id: int = Query(...),
        session: dict[str, Any] = Depends(current_session),
        store: SQLiteStore = Depends(get_read_store),
    ) -> dict[str, Any]:
        detail = store.get_snapshot_detail(snapshot_id, code)
        if detail is None:
            raise ApiError("快照或候选不存在", 404, "not_found")
        if detail["candidate"] is None:
            raise ApiError("候选不在该快照中", 404, "not_found")
        return {
            "snapshot_id": detail["id"],
            "source_ts": detail["source_ts"],
            "generated_at": detail["generated_at"],
            "health": detail["health"],
            "overall_weak": detail["overall_weak"],
            "candidate": detail["candidate"],
        }

    @router.get("/history")
    async def history(
        request: Request,
        limit: int = Query(20, ge=1, le=100),
        cursor: int | None = Query(None, ge=0),
        from_date: str | None = Query(None, alias="from"),
        to: str | None = Query(None),
        code: str | None = Query(None),
        repeat_active: bool | None = Query(None),
        session: dict[str, Any] = Depends(current_session),
        store: SQLiteStore = Depends(get_read_store),
    ) -> dict[str, Any]:
        rows = store.query_snapshots(
            limit=limit,
            cursor=cursor,
            from_date=_parse_date(from_date),
            to_date=_parse_date(to),
            code=code,
            repeat_active=repeat_active,
        )
        items_by_snapshot = store.query_snapshot_items([int(row["id"]) for row in rows])
        tracker = CandidateRepeatTracker(store)
        items = []
        with store.connect() as connection:
            for row in rows:
                snapshot_id = int(row["id"])
                trade_date = None
                parsed = parse_shanghai_timestamp(row["source_ts"])
                if parsed is not None:
                    trade_date = parsed.date()
                displayed = items_by_snapshot.get(snapshot_id, [])
                candidates = []
                for candidate in displayed:
                    payload = {
                        "rank": candidate["rank"],
                        "code": candidate["code"],
                        "name": candidate["name"],
                        "level": candidate["level"],
                        "is_formal": candidate["is_formal"],
                        "is_supplement": candidate["is_supplement"],
                        "price": candidate["price"],
                        "change_pct": candidate["change_pct"],
                        "sector_name": candidate["sector_name"],
                    }
                    if trade_date is not None:
                        payload.update(
                            tracker.historical_fields_for(
                                connection,
                                code=str(candidate["code"]),
                                trade_date=trade_date,
                            )
                        )
                    else:
                        payload.update(empty_repeat_fields())
                    candidates.append(payload)
                items.append(
                    {
                        "snapshot_id": snapshot_id,
                        "source_ts": row["source_ts"],
                        "generated_at": row["generated_at"],
                        "health": row["health"],
                        "overall_weak": row["overall_weak"],
                        "candidates": candidates,
                    }
                )
        return {"items": items, "next_cursor": items[-1]["snapshot_id"] if items else None}

    @router.get("/outcomes")
    async def outcomes(
        request: Request,
        range_name: str = Query("month", alias="range"),
        session: dict[str, Any] = Depends(current_session),
        store: SQLiteStore = Depends(get_read_store),
    ) -> dict[str, Any]:
        ranges: dict[str, int | None] = {"week": 5, "month": 20, "all": None}
        if range_name not in ranges:
            raise ApiError("复盘范围无效", 400, "invalid_outcome_range")
        records = candidate_outcome_rows(store, trading_days=ranges[range_name])
        review = build_outcome_review(records)
        return {
            "range": range_name,
            "summary": _outcome_stats_payload(review.overall),
            "morning": _outcome_stats_payload(review.morning),
            "afternoon": _outcome_stats_payload(review.afternoon),
            "portfolio": {
                "win_rate": review.portfolio_win_rate,
                "complete_days": review.complete_portfolio_days,
                "win_days": review.portfolio_win_days,
                "days": [
                    {
                        "entry_trade_date": row.entry_trade_date.isoformat(),
                        "total_count": row.total_count,
                        "settled_count": row.settled_count,
                        "complete": row.complete,
                        "average_return_pct": row.average_return_pct,
                        "won": row.won,
                    }
                    for row in review.portfolios
                ],
            },
            "records": [
                {
                    "id": row.id,
                    "entry_trade_date": row.entry_trade_date.isoformat(),
                    "slot": row.slot.value,
                    "rank": row.rank,
                    "code": row.code,
                    "name": row.name,
                    "entry_price": row.entry_price,
                    "target_trade_date": (
                        row.target_trade_date.isoformat() if row.target_trade_date else None
                    ),
                    "exit_price": row.exit_price,
                    "return_pct": row.return_pct,
                    "status": row.status.value,
                    "outcome": row.outcome.value if row.outcome else None,
                    "settlement_method": (
                        row.settlement_method.value if row.settlement_method else None
                    ),
                    "display_reason": _outcome_reason(row.status, row.safe_reason),
                }
                for row in review.records
            ],
            "backfill": _backfill_payload(
                store.get_app_setting("candidate_outcome_backfill_status")
            ),
        }

    @router.get("/alerts")
    async def alerts(
        request: Request,
        limit: int = Query(20, ge=1, le=100),
        cursor: int | None = Query(None, ge=0),
        from_date: str | None = Query(None, alias="from"),
        to: str | None = Query(None),
        trigger_type: str | None = Query(None),
        session: dict[str, Any] = Depends(current_session),
        store: SQLiteStore = Depends(get_read_store),
    ) -> dict[str, Any]:
        rows = store.query_alert_events(
            limit=limit,
            cursor=cursor,
            from_date=_parse_date(from_date),
            to_date=_parse_date(to),
            trigger_type=trigger_type,
        )
        items = []
        for row in rows:
            detail = json.loads(row["detail_json"]) if row["detail_json"] else {}
            payload = json.loads(row["payload_json"]) if row["payload_json"] else {}
            items.append(
                {
                    "alert_id": row["alert_id"],
                    "snapshot_id": row["snapshot_id"],
                    "displayed_at": row["displayed_at"],
                    "decision": row["decision"],
                    "trigger_type": row["trigger_type"],
                    "source_ts": row["source_ts"],
                    "overall_weak": row["overall_weak"],
                    "triggering_codes": detail.get("trigger_symbol")
                    or [c["code"] for c in payload.get("candidates", [])][:1],
                    "detail": detail,
                }
            )
        return {"items": items, "next_cursor": items[-1]["alert_id"] if items else None}

    @router.get("/summaries")
    async def summaries(
        request: Request,
        limit: int = Query(20, ge=1, le=100),
        cursor: str | None = Query(None),
        session: dict[str, Any] = Depends(current_session),
        store: SQLiteStore = Depends(get_read_store),
    ) -> dict[str, Any]:
        since = _now().date() - timedelta(days=31)
        rows = store.list_daily_summaries(since=since)
        items = [
            {
                "trade_date": row["trade_date"],
                "generated_at": row["generated_at"],
                "alert_count": row["alert_count"],
                "version": row["version"],
                "catch_up": bool(row.get("catch_up", 0)),
            }
            for row in rows[:limit]
        ]
        return {"items": items, "next_cursor": None}

    @router.get("/summaries/{trade_date}")
    async def summary_detail(
        trade_date: str,
        session: dict[str, Any] = Depends(current_session),
        store: SQLiteStore = Depends(get_read_store),
    ) -> dict[str, Any]:
        summary = store.get_daily_summary(trade_date)
        if summary is None:
            raise ApiError("当日总结不存在", 404, "not_found")
        return summary

    @router.get("/summaries/{trade_date}/pdf")
    async def summary_pdf(
        trade_date: str,
        request: Request,
        session: dict[str, Any] = Depends(current_session),
        store: SQLiteStore = Depends(get_read_store),
    ) -> Response:
        report_dir = Path(request.app.state.settings.report_dir)
        pdf = report_dir / f"{trade_date}-A股盘后回顾.pdf"
        if not pdf.is_file() or not _safe_name(trade_date):
            raise ApiError("PDF 不存在", 404, "not_found")
        return FileResponse(
            pdf,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'inline; filename="stockwatcher-{trade_date}.pdf"',
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "private, no-store",
            },
        )

    return router


def _safe_name(value: str) -> bool:
    import re

    return re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is not None


# -- commands router -------------------------------------------------------


def commands_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["commands"])

    @router.post("/commands/manual-refresh")
    async def manual_refresh(
        request: Request,
        session: dict[str, Any] = Depends(require_csrf),
        commands: CommandService = Depends(get_commands),
        auth: AuthService = Depends(get_auth),
        audit: AuditLogRepository = Depends(get_audit),
    ) -> Response:
        try:
            auth.command_limiter.consume(f"cmd:{session['user_id']}")
        except AuthError as error:
            return error_response(request, str(error), error.status_code, "rate_limited")
        idempotency = request.headers.get("Idempotency-Key") or str(uuid.uuid4())
        command = commands.create(
            command_type=CommandType.MANUAL_REFRESH,
            requested_by=int(cast(int, session["user_id"])),
            idempotency_key=idempotency,
        )
        audit.record(
            actor_user_id=session["user_id"],
            action="command.create",
            object_type="command",
            object_id=str(command["command_id"]),
            outcome="succeeded",
            request_id=request.headers.get("X-Request-Id"),
            detail={
                "command_type": "manual_refresh",
                "coalesced": bool(command.get("coalesced", False)),
            },
        )
        return JSONResponse(
            status_code=202,
            content={
                "command_id": command["command_id"],
                "command_type": "manual_refresh",
                "status": command["status"],
                "coalesced": bool(command.get("coalesced", False)),
            },
            headers={"Cache-Control": "no-store"},
        )

    @router.get("/commands/{command_id}")
    async def command_status(
        command_id: str,
        session: dict[str, Any] = Depends(current_session),
        commands: CommandService = Depends(get_commands),
    ) -> dict[str, Any]:
        command = commands.get(command_id)
        if command is None:
            raise ApiError("命令不存在", 404, "not_found")
        is_owner = int(command["requested_by"]) == int(cast(int, session["user_id"]))
        is_admin = session.get("role") == "admin"
        if not is_owner and not is_admin:
            if command["command_type"] != CommandType.MANUAL_REFRESH.value:
                raise ApiError("命令不存在", 404, "not_found")
            return {
                "command_id": command["command_id"],
                "command_type": command["command_type"],
                "status": command["status"],
                "coalesced": True,
            }
        return {
            "command_id": command["command_id"],
            "command_type": command["command_type"],
            "status": command["status"],
            "requested_at": command["requested_at"],
            "started_at": command["started_at"],
            "completed_at": command["completed_at"],
            "attempts": command["attempts"],
            "error_code": command["error_code"],
            "result": command.get("result"),
            "coalesced": bool(command.get("coalesced", False)),
        }

    return router


# -- admin router ----------------------------------------------------------


def admin_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/admin", tags=["admin"])

    @router.get("/diagnostics")
    async def diagnostics(
        request: Request,
        session: dict[str, Any] = Depends(require_admin),
        store: SQLiteStore = Depends(get_store),
        public_state: PublicStateBuilder = Depends(get_public_state),
        outbox: EventOutbox = Depends(get_outbox),
    ) -> dict[str, Any]:
        return _diagnostics_payload(store, outbox, public_state)

    @router.get("/scan-runs")
    async def scan_runs(
        request: Request,
        trade_date: str | None = Query(None),
        limit: int = Query(100, ge=1, le=500),
        session: dict[str, Any] = Depends(require_admin),
        store: SQLiteStore = Depends(get_read_store),
    ) -> dict[str, Any]:
        rows = store.list_scan_runs(trade_date or _now().date().isoformat())
        item_keys = (
            "id",
            "started_at",
            "completed_at",
            "trigger_type",
            "health",
            "coverage_ratio",
            "elapsed_seconds",
            "source_age_seconds",
            "detail",
            "task_key",
        )
        return {
            "items": [
                {key: row[key] for key in item_keys} for row in rows[-limit:]
            ],
            "next_cursor": None,
        }

    @router.get("/scan-runs/{scan_id}/audit")
    async def scan_audit(
        scan_id: int,
        session: dict[str, Any] = Depends(require_admin),
        store: SQLiteStore = Depends(get_read_store),
    ) -> dict[str, Any]:
        run = store.get_scan_run(scan_id)
        if run is None:
            raise ApiError("扫描记录不存在", 404, "not_found")
        try:
            audit = json.loads(run["audit_json"] or "{}")
        except json.JSONDecodeError:
            audit = {}
        return {
            "scan_id": scan_id,
            "started_at": run["started_at"],
            "completed_at": run["completed_at"],
            "trigger_type": run["trigger_type"],
            "health": run["health"],
            "raw_top20": _raw_top20_from_audit(audit),
            "raw_top3_codes": audit.get("raw_codes", []),
            "stable_codes": audit.get("stable_codes", []),
            "excluded_count": len(audit.get("rows", [])) - 20
            if isinstance(audit.get("rows"), list)
            else None,
            "audit": audit,
        }

    @router.post("/token/test")
    async def token_test(
        request: Request,
        payload: TokenPayload,
        session: dict[str, Any] = Depends(require_admin_csrf),
        secrets: SecretService | None = Depends(get_secrets),
        commands: CommandService = Depends(get_commands),
        audit: AuditLogRepository = Depends(get_audit),
    ) -> Response:
        candidate = payload.token
        if secrets is None:
            return error_response(request, "密钥服务不可用", 503, "secrets_unavailable")
        if not candidate:
            return error_response(request, "Token 不能为空", 400, "invalid_token")
        created = secrets.create_request(
            candidate_token=candidate,
            purpose="token_test",
            requested_by=int(cast(int, session["user_id"])),
        )
        command = commands.create(
            command_type=CommandType.TOKEN_TEST,
            requested_by=int(cast(int, session["user_id"])),
            secret_request_id=created["request_id"],
            idempotency_key=f"token-test-{uuid.uuid4().hex[:16]}",
        )
        audit.record(
            actor_user_id=session["user_id"],
            action="token.test",
            object_type="secret_request",
            object_id=created["request_id"],
            outcome="succeeded",
            detail={"fingerprint": created["fingerprint"]},
        )
        return JSONResponse(
            status_code=202,
            content={
                "command_id": command["command_id"],
                "fingerprint": created["fingerprint"],
                "status": "queued",
            },
            headers={"Cache-Control": "no-store"},
        )

    @router.put("/token")
    async def token_update(
        request: Request,
        payload: TokenPayload,
        session: dict[str, Any] = Depends(require_admin_csrf),
        secrets: SecretService | None = Depends(get_secrets),
        commands: CommandService = Depends(get_commands),
        audit: AuditLogRepository = Depends(get_audit),
    ) -> Response:
        candidate = payload.token
        if secrets is None:
            return error_response(request, "密钥服务不可用", 503, "secrets_unavailable")
        if not candidate:
            return error_response(request, "Token 不能为空", 400, "invalid_token")
        created = secrets.create_request(
            candidate_token=candidate,
            purpose="token_update",
            requested_by=int(cast(int, session["user_id"])),
        )
        command = commands.create(
            command_type=CommandType.TOKEN_UPDATE,
            requested_by=int(cast(int, session["user_id"])),
            secret_request_id=created["request_id"],
            idempotency_key=f"token-update-{uuid.uuid4().hex[:16]}",
        )
        audit.record(
            actor_user_id=session["user_id"],
            action="token.update",
            object_type="secret_request",
            object_id=created["request_id"],
            outcome="succeeded",
            detail={"fingerprint": created["fingerprint"]},
        )
        return JSONResponse(
            status_code=202,
            content={
                "command_id": command["command_id"],
                "fingerprint": created["fingerprint"],
                "status": "queued",
                "note": "先测后激活；失败保留旧 Token",
            },
            headers={"Cache-Control": "no-store"},
        )

    @router.post("/cache/refresh")
    async def cache_refresh(
        request: Request,
        session: dict[str, Any] = Depends(require_admin_csrf),
        commands: CommandService = Depends(get_commands),
        audit: AuditLogRepository = Depends(get_audit),
    ) -> Response:
        command = commands.create(
            command_type=CommandType.UNIVERSE_REFRESH,
            requested_by=int(cast(int, session["user_id"])),
            payload={"force": True},
            idempotency_key=f"universe-refresh-{uuid.uuid4().hex[:16]}",
        )
        audit.record(
            actor_user_id=session["user_id"],
            action="cache.refresh",
            object_type="command",
            object_id=str(command["command_id"]),
            outcome="succeeded",
        )
        return JSONResponse(
            status_code=202,
            content={
                "command_id": command["command_id"],
                "status": "queued",
            },
            headers={"Cache-Control": "no-store"},
        )

    @router.get("/users")
    async def users_list(
        request: Request,
        session: dict[str, Any] = Depends(require_admin),
        users: UserRepository = Depends(get_users),
    ) -> dict[str, Any]:
        rows = users.list_users()
        return {
            "items": [
                {
                    "user_id": row["user_id"],
                    "username": row["username"],
                    "role": row["role"],
                    "active": bool(row["active"]),
                    "created_at": row["created_at"],
                    "last_login_at": row["last_login_at"],
                }
                for row in rows
            ]
        }

    @router.post("/users")
    async def users_create(
        request: Request,
        payload: UserCreatePayload,
        session: dict[str, Any] = Depends(require_admin_csrf),
        auth: AuthService = Depends(get_auth),
    ) -> Response:
        try:
            user = await asyncio.to_thread(
                auth.create_user,
                username=payload.username,
                password=payload.password,
                role=payload.role,
                created_by=int(cast(int, session["user_id"])),
            )
        except AuthError as error:
            return error_response(request, str(error), error.status_code, "user_create_failed")
        return JSONResponse(
            status_code=201,
            content={
                "user_id": user["user_id"],
                "username": user["username"],
                "role": user["role"],
            },
        )

    @router.patch("/users/{user_id}")
    async def users_update(
        user_id: int,
        request: Request,
        payload: UserUpdatePayload,
        session: dict[str, Any] = Depends(require_admin_csrf),
        auth: AuthService = Depends(get_auth),
    ) -> Response:
        role = payload.role
        active = payload.active
        password = payload.password
        password_hash: str | None = None
        if password is not None:
            try:
                password_hash = await asyncio.to_thread(
                    auth.hash_password,
                    str(password),
                )
            except AuthError as error:
                return error_response(
                    request, str(error), error.status_code, "invalid_password"
                )
        try:
            result = await asyncio.to_thread(
                auth.update_user_security,
                actor_user_id=int(cast(int, session["user_id"])),
                user_id=user_id,
                role=role,
                active=active,
                password_hash=password_hash,
            )
        except LastActiveAdminError:
            return error_response(
                request,
                "必须至少保留一个启用的管理员",
                409,
                "last_admin_required",
            )
        if result is None:
            return error_response(request, "用户不存在", 404, "not_found")
        if result.security_changed:
            await request.app.state.ws_manager.disconnect_user(
                user_id,
                code=4403 if result.role_changed else 4401,
                reason="authorization changed" if result.role_changed else "session revoked",
            )
        updated = result.user
        return JSONResponse(
            status_code=200,
            content={
                "user_id": updated["user_id"],
                "username": updated["username"],
                "role": updated["role"],
                "active": bool(updated["active"]),
            },
        )

    return router


def _raw_top20_from_audit(audit: dict[str, Any]) -> list[dict[str, Any]]:
    rows = audit.get("rows")
    if not isinstance(rows, list):
        return []

    def rank_value(row: dict[str, Any]) -> int:
        value = cast(int, row.get("raw_rank"))
        try:
            return int(value)
        except (TypeError, ValueError):
            return 10**9

    ranked = sorted(rows, key=rank_value)
    return [
        {
            "rank": index,
            "code": str(row.get("code", "")),
            "name": str(row.get("name", "")),
            "sector": str(row.get("sector", "")),
            "sector_type": str(row.get("sector_type", "")),
            "level": str(row.get("level", "")),
            "total_score": row.get("total_score"),
            "is_formal": bool(row.get("is_formal", False)),
            "decision": str(row.get("decision", "")),
        }
        for index, row in enumerate(ranked[:20], start=1)
    ]


def _diagnostics_payload(
    store: SQLiteStore,
    outbox: EventOutbox,
    public_state: PublicStateBuilder,
) -> dict[str, Any]:
    now = _now()
    with store.connect() as connection:
        lease = connection.execute(
            "SELECT holder_id, source_commit, acquired_at, heartbeat_at, expires_at, "
            "fencing_token FROM service_leases WHERE lease_name = 'stockwatcher-worker'"
        ).fetchone()
        db_size = connection.execute(
            "PRAGMA page_count"
        ).fetchone()[0] * connection.execute("PRAGMA page_size").fetchone()[0]
        wal_row = connection.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
    worker: dict[str, Any] = {
        "lease": (
            {
                "holder_id": lease[0],
                "source_commit": lease[1],
                "acquired_at": lease[2],
                "heartbeat_at": lease[3],
                "expires_at": lease[4],
                "fencing_token": lease[5],
            }
            if lease is not None
            else None
        ),
        "heartbeat_age_seconds": (
            (now - datetime.fromisoformat(lease[3])).total_seconds()
            if lease is not None and lease[3]
            else None
        ),
    }
    public = store.read_public_state()
    return {
        "worker": worker,
        "database": {
            "schema_version": store._schema_version(store.connect()),
            "size_bytes": db_size,
            "wal_frames": wal_row[1] if wal_row else None,
            "events_latest_id": outbox.latest_id(),
            "events_minimum_id": outbox.minimum_available_id(),
        },
        "data_sources": {
            "active_fingerprint": None,
            "mode": "tushare_15000",
            "note": "Token fingerprint 仅 Admin 页面可见；此处不返回任何秘密。",
        },
        "public_state": public,
    }
