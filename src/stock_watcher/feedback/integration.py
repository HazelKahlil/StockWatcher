"""Small adapter to the reviewed StockWatcher Web app; disabled by default."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.templating import Jinja2Templates

from .api import approval_router
from .repository import ApprovalRepository

FEATURE_ENV = "STOCKWATCHER_CANDIDATE_APPROVALS"


def enabled_from_environment() -> bool:
    value = os.environ.get(FEATURE_ENV, "0").strip().lower()
    if value not in {"0", "1", "false", "true"}:
        raise ValueError(f"{FEATURE_ENV} must be 0/1/false/true")
    return value in {"1", "true"}


def install_approval_feature(app: FastAPI, templates: Jinja2Templates, settings: Any) -> None:
    enabled = enabled_from_environment()
    templates.env.globals["candidate_approvals_enabled"] = enabled
    if not enabled:
        return
    # Lazy imports avoid changing the application's existing authentication flow.
    from stock_watcher.server.api import current_session, require_admin, require_csrf

    repository = ApprovalRepository(Path(settings.db_path))
    app.include_router(approval_router(repository, current_session, require_csrf, require_admin))
    # Deliberately no DDL, no provider, no outbox broadcasts and no Worker command.
