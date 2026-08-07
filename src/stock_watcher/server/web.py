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
    generate_opaque_token,
)

from .api import (
    ApiError,
    admin_router,
    auth_router,
    commands_router,
    error_response,
    state_router,
)
from .auth import SESSION_COOKIE_NAME, AuthService
from .config import ServerSettings
from .ws import WebSocketManager

logger = logging.getLogger("stock_watcher.server")

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent / "static"


def _build_services(settings: ServerSettings) -> dict[str, Any]:
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    settings.report_dir.mkdir(parents=True, exist_ok=True)
    store = SQLiteStore(settings.db_path)
    store.initialize()
    commit = settings.source_commit if settings.source_commit != "unknown" else source_commit()
    outbox = EventOutbox(store, source_commit=commit)
    commands = CommandService(store)
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
        "outbox": outbox,
        "commands": commands,
        "auth": auth,
        "secrets": secrets,
        "audit": AuditLogRepository(store),
        "users": UserRepository(store),
        "user_state": UserStateRepository(store),
        "public_state": PublicStateBuilder(store),
    }


def create_app(settings: ServerSettings | None = None) -> FastAPI:
    app_settings = settings or ServerSettings.from_env()
    services = _build_services(app_settings)
    store: SQLiteStore = services["store"]
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
        yield
        store.initialize()

    app = FastAPI(
        title="StockWatcher Web Internal Test",
        version="1.0.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/v1/openapi.json",
    )
    for key, value in services.items():
        setattr(app.state, key, value)
    app.state.ws_manager = ws_manager

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
            store.initialize()
            with store.connect() as connection:
                version = connection.execute(
                    "SELECT version FROM schema_version ORDER BY rowid DESC LIMIT 1"
                ).fetchone()
            if version is None or int(version[0]) != store.CURRENT_SCHEMA_VERSION:
                return JSONResponse(status_code=503, content={"status": "not_ready"})
            lease_row = None
            with store.connect() as connection:
                lease_row = connection.execute(
                    "SELECT heartbeat_at FROM service_leases "
                    "WHERE lease_name = 'stockwatcher-worker'"
                ).fetchone()
            return JSONResponse(
                status_code=200,
                content={
                    "status": "ready",
                    "schema_version": int(version[0]),
                    "worker_lease_held": lease_row is not None,
                    "degraded": lease_row is None,
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
    """Server-rendered pages receive the plaintext CSRF value once.

    The database stores only the hash; the value is kept in the browser's JS
    memory and submitted via the X-CSRF-Token header.
    """
    if token is None:
        return ""
    session = auth.sessions.get(token)
    if session is None:
        return ""
    import hashlib

    # Login returns the plaintext CSRF in the JSON body; for server-rendered
    # pages we persist nothing extra: the value is re-derived per page by
    # generating a new opaque value and storing only its hash.
    plaintext = generate_opaque_token()
    auth.sessions.store.transaction()
    with auth.sessions.store.transaction() as connection:
        connection.execute(
            "UPDATE web_sessions SET csrf_token_hash = ? "
            "WHERE session_token_hash = ?",
            (hashlib.sha256(plaintext.encode("utf-8")).hexdigest(), session["session_token_hash"]),
        )
    return plaintext


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
