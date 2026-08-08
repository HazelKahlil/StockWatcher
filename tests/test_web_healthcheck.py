"""Container healthchecks must remain read-only against the shared SQLite DB."""
from __future__ import annotations

from pathlib import Path

import pytest

from stock_watcher.server import healthcheck
from stock_watcher.server.config import ServerSettings
from stock_watcher.storage import SQLiteStore


def _settings(tmp_path: Path) -> ServerSettings:
    db = tmp_path / "db" / "stockwatcher.db"
    db.parent.mkdir(parents=True)
    SQLiteStore(db).initialize()
    return ServerSettings(
        db_path=db,
        report_dir=tmp_path / "reports",
        public_origin="http://testserver",
    )


def test_web_healthcheck_opens_read_only_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    observed: list[bool] = []
    original_init = SQLiteStore.__init__

    def spy(self: SQLiteStore, path: Path, read_only: bool = False) -> None:
        observed.append(read_only)
        original_init(self, path, read_only=read_only)

    monkeypatch.setattr(SQLiteStore, "__init__", spy)
    assert healthcheck.check_web(settings) == 0
    assert observed == [True]


def test_worker_healthcheck_opens_read_only_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    observed: list[bool] = []
    original_init = SQLiteStore.__init__

    def spy(self: SQLiteStore, path: Path, read_only: bool = False) -> None:
        observed.append(read_only)
        original_init(self, path, read_only=read_only)

    monkeypatch.setattr(SQLiteStore, "__init__", spy)
    assert healthcheck.check_worker(settings) == 1
    assert observed == [True]
