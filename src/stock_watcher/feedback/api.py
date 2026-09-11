"""Router factory. Production authentication dependencies are supplied by StockWatcher."""
from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .repository import MAX_SQLITE_INT, ApprovalCommand, ApprovalRepository, FeedbackError
from .schema import SchemaUnavailable


class ApprovalPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    snapshot_id: int = Field(gt=0, le=MAX_SQLITE_INT)
    selected: bool
    request_id: str = Field(min_length=16, max_length=96, pattern=r"^[A-Za-z0-9_-]+$")
    expected_version: int = Field(ge=0, lt=MAX_SQLITE_INT)
    surface: Literal["dashboard", "history"] = "dashboard"


def response_for(action: Callable[[], dict[str, Any]]) -> JSONResponse:
    headers = {"Cache-Control": "private, no-store"}
    try:
        return JSONResponse(action(), headers=headers)
    except FeedbackError as error:
        if error.status == 429:
            headers["Retry-After"] = "60"
        return JSONResponse(
            {"error": {"code": error.code, "message": str(error)}},
            status_code=error.status, headers=headers,
        )
    except (SchemaUnavailable, sqlite3.Error, OSError):
        # A failed feedback extension must not affect the common market projection.
        return JSONResponse(
            {
                "error": {
                    "code": "feedback_unavailable",
                    "message": "反馈暂不可用，候选观察不受影响",
                }
            },
            status_code=503,
            headers={**headers, "Retry-After": "3"},
        )


def approval_router(
    repository: ApprovalRepository,
    read_session: Callable[..., Any],
    write_session: Callable[..., Any],
    admin_session: Callable[..., Any],
) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["candidate-approvals"])

    # Sync endpoints run SQLite work in FastAPI's threadpool, not the event loop.
    @router.get("/me/candidate-approvals/state")
    def state(
        snapshot_id: int = Query(gt=0, le=MAX_SQLITE_INT),
        session: dict[str, Any] = Depends(read_session),
    ) -> JSONResponse:
        return response_for(lambda: repository.snapshot_state(int(session["user_id"]), snapshot_id))

    @router.put("/me/candidate-approvals/{code}")
    def set_approval(
        code: str,
        payload: ApprovalPayload,
        session: dict[str, Any] = Depends(write_session),
    ) -> JSONResponse:
        command = ApprovalCommand(code=code, **payload.model_dump())
        return response_for(lambda: repository.apply(int(session["user_id"]), command))

    @router.get("/me/candidate-approvals/events")
    def my_events(
        cursor: int | None = Query(None, gt=0, le=MAX_SQLITE_INT),
        limit: int = Query(50, ge=1, le=100),
        from_date: date | None = Query(None, alias="from"),
        to_date: date | None = Query(None, alias="to"),
        session: dict[str, Any] = Depends(read_session),
    ) -> JSONResponse:
        return response_for(lambda: repository.events(
            int(session["user_id"]), cursor=cursor, limit=limit,
            from_date=from_date, to_date=to_date,
        ))

    @router.get("/admin/candidate-approvals/events")
    def admin_events(
        cursor: int | None = Query(None, gt=0, le=MAX_SQLITE_INT),
        limit: int = Query(50, ge=1, le=100),
        from_date: date | None = Query(None, alias="from"),
        to_date: date | None = Query(None, alias="to"),
        session: dict[str, Any] = Depends(admin_session),
    ) -> JSONResponse:
        return response_for(lambda: repository.events(
            int(session["user_id"]), cursor=cursor, limit=limit,
            from_date=from_date, to_date=to_date, admin=True,
        ))

    return router
