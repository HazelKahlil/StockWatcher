"""FastAPI web application: single process, read-only projections + commands.

Entry point: ``python -m stock_watcher.server.web``.
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from stock_watcher.build_info import source_commit
from stock_watcher.services import CommandService, EventOutbox, SecretService
from stock_watcher.services.public_state import PublicStateBuilder
from stock_watcher.services.secret_service import load_master_key
from stock_watcher.storage import SQLiteStore
from stock_watcher.storage.web import (
    AuditLogRepository,
    UserRepository,
    UserStateRepository,
)

from .api import (
    ApiError,
    admin_router,
    auth_router,
    commands_router,
    error_response,
    state_router,
)
from .auth import SESSION_COOKIE_NAME, AuthService, csrf_value_for_session
from .config import ServerSettings
from .healthcheck import worker_readiness
from .ws import WebSocketManager

logger = logging.getLogger("stock_watcher.server")

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent / "static"


def _build_services(settings: ServerSettings) -> dict[str, Any]:
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    settings.report_dir.mkdir(parents=True, exist_ok=True)
    store = SQLiteStore(
        settings.db_path,
        recovery_backup_dirs=(settings.backup_dir,),
    )
    store.initialize()
    # Worker-owned rows must be read through short-lived read-only connections.
    # A long-lived Web connection can retain an obsolete WAL view across a
    # Worker/container restart even though a fresh reader sees the new rows.
    read_store = SQLiteStore(settings.db_path, read_only=True)
    commit = settings.source_commit if settings.source_commit != "unknown" else source_commit()
    outbox = EventOutbox(store, read_store=read_store, source_commit=commit)
    commands = CommandService(store, read_store=read_store)
    auth = AuthService(
        store,
        absolute_hours=settings.session_absolute_hours,
        idle_minutes=settings.session_idle_minutes,
    )
    secrets: SecretService | None = None
    if settings.master_key_file is not None:
        secrets = SecretService(
            store,
            master_key=load_master_key(settings.master_key_file),
            environment=settings.environment,
            key_version=settings.secret_key_version,
        )
    return {
        "settings": settings,
        "store": store,
        "read_store": read_store,
        "outbox": outbox,
        "commands": commands,
        "auth": auth,
        "secrets": secrets,
        "audit": AuditLogRepository(store),
        "users": UserRepository(store),
        "user_state": UserStateRepository(store),
        "public_state": PublicStateBuilder(read_store),
    }


def create_app(settings: ServerSettings | None = None) -> FastAPI:
    app_settings = settings or ServerSettings.from_env()
    services = _build_services(app_settings)
    store: SQLiteStore = services["store"]
    read_store: SQLiteStore = services["read_store"]
    outbox: EventOutbox = services["outbox"]
    auth: AuthService = services["auth"]
    ws_manager = WebSocketManager(
        store,
        outbox,
        services["public_state"],
        source_commit=(
            app_settings.source_commit
            if app_settings.source_commit != "unknown"
            else source_commit()
        ),
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> Any:
        try:
            yield
        finally:
            store.close()
            read_store.close()

    app = FastAPI(
        title="StockWatcher Web Internal Test",
        version="1.0.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=(
            None
            if app_settings.environment == "production"
            else "/api/v1/openapi.json"
        ),
    )
    for key, value in services.items():
        setattr(app.state, key, value)
    app.state.ws_manager = ws_manager

    @app.middleware("http")
    async def harden_http_responses(request: Request, call_next: Any) -> Any:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
        )
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        if not request.url.path.startswith("/static/"):
            response.headers.setdefault("Cache-Control", "private, no-store")
        return response

    for router in (
        auth_router(),
        state_router(),
        commands_router(),
        admin_router(),
    ):
        app.include_router(router)

    templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # -- exception handling ----------------------------------------------

    @app.exception_handler(ApiError)
    async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
        return error_response(request, str(exc), exc.status_code, exc.code)

    # -- health -----------------------------------------------------------

    @app.get("/health/live")
    async def health_live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    async def health_ready() -> JSONResponse:
        try:
            with read_store.connect() as connection:
                version = connection.execute(
                    "SELECT version FROM schema_version ORDER BY rowid DESC LIMIT 1"
                ).fetchone()
            if version is None or int(version[0]) != store.CURRENT_SCHEMA_VERSION:
                return JSONResponse(status_code=503, content={"status": "not_ready"})
            worker_ready, worker_status = worker_readiness(read_store, app_settings)
            if not worker_ready:
                return JSONResponse(
                    status_code=503,
                    content={
                        "status": "not_ready",
                        "schema_version": int(version[0]),
                        **worker_status,
                    },
                )
            return JSONResponse(
                status_code=200,
                content={
                    "status": "ready",
                    "schema_version": int(version[0]),
                    **worker_status,
                    "degraded": False,
                },
            )
        except Exception as error:
            return JSONResponse(
                status_code=503,
                content={"status": "not_ready", "detail": type(error).__name__},
            )

    # -- pages ------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        token = request.cookies.get(SESSION_COOKIE_NAME)
        session = auth.authenticate(token) if token else None
        if session is None:
            return templates.TemplateResponse(
                request,
                "login.html",
                {"public_origin": app_settings.public_origin},
            )
        return templates.TemplateResponse(
            request,
            "dashboard.html",
            {"user": session, "csrf": _csrf_for_session(auth, token)},
        )

    @app.get("/alerts", response_class=HTMLResponse)
    async def alerts_page(request: Request) -> HTMLResponse:
        return await _page(request, "alerts.html", auth, templates)

    @app.get("/history", response_class=HTMLResponse)
    async def history_page(request: Request) -> HTMLResponse:
        return await _page(request, "history.html", auth, templates)

    @app.get("/outcomes", response_class=HTMLResponse)
    async def outcomes_page(request: Request) -> HTMLResponse:
        return await _page(request, "outcomes.html", auth, templates)

    @app.get("/summary", response_class=HTMLResponse)
    async def summary_page(request: Request) -> HTMLResponse:
        return await _page(request, "summary.html", auth, templates)

    @app.get("/admin", response_class=HTMLResponse)
    async def admin_page(request: Request) -> HTMLResponse:
        token = request.cookies.get(SESSION_COOKIE_NAME)
        session = auth.authenticate(token) if token else None
        if session is None:
            return templates.TemplateResponse(
                request,
                "login.html",
                {"public_origin": app_settings.public_origin},
            )
        if session.get("role") != "admin":
            return templates.TemplateResponse(
                request,
                "forbidden.html",
                {"user": session},
            )
        return templates.TemplateResponse(
            request,
            "admin.html",
            {"user": session, "csrf": _csrf_for_session(auth, token)},
        )

    # -- WebSocket --------------------------------------------------------

    @app.websocket("/ws/v1/events")
    async def ws_events(websocket: WebSocket) -> None:
        token = websocket.cookies.get(SESSION_COOKIE_NAME)
        session = auth.authenticate(token) if token else None
        if session is None:
            await websocket.close(code=4401, reason="unauthorized")
            return
        origin = websocket.headers.get("origin", "")
        if origin and _origin_mismatch(origin, app_settings.public_origin):
            await websocket.close(code=4403, reason="origin rejected")
            return
        await ws_manager.handle(websocket, session)

    return app


def _csrf_for_session(auth: AuthService, token: str | None) -> str:
    """Return the stable per-session CSRF value for server-rendered pages."""
    if token is None:
        return ""
    session = auth.sessions.get(token)
    if session is None:
        return ""
    return csrf_value_for_session(token)


def _origin_mismatch(origin: str, public_origin: str) -> bool:
    try:
        from urllib.parse import urlparse

        public_host = urlparse(public_origin).netloc
        origin_host = urlparse(origin).netloc
        return origin_host != public_host
    except ValueError:
        return True


async def _page(
    request: Request,
    template: str,
    auth: AuthService,
    templates: Jinja2Templates,
) -> HTMLResponse:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    session = auth.authenticate(token) if token else None
    if session is None:
        return templates.TemplateResponse(
            request,
            "login.html",
            {"public_origin": request.app.state.settings.public_origin},
        )
    return templates.TemplateResponse(
        request,
        template,
        {"user": session, "csrf": _csrf_for_session(auth, token)},
    )


def main() -> None:
    import uvicorn

    settings = ServerSettings.from_env()
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
    port = int(os.environ.get("STOCKWATCHER_WEB_PORT", "8000"))
    host = os.environ.get("STOCKWATCHER_WEB_HOST", "0.0.0.0")
    app = create_app(settings)
    uvicorn.run(app, host=host, port=port, workers=1, log_config=None)


if __name__ == "__main__":
    main()
