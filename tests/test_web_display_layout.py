"""Display-density UI regression. Does not overwrite committed evidence."""

from __future__ import annotations

import socket
from pathlib import Path

import pytest
from web_display_harness import (
    assert_no_extra_refresh,
    chromium_available,
    harness_server,
    refresh_posts_in,
    require_ui,
    run_dashboard_interactions,
    run_geometry,
)


def _need_chromium() -> None:
    if chromium_available():
        return
    if require_ui():
        pytest.fail("Playwright Chromium is required in this job")
    pytest.skip("Playwright Chromium is not installed")


def test_web_display_geometry(tmp_path: Path) -> None:
    _need_chromium()
    assert run_geometry(out_dir=tmp_path, write_shots=False, prefix="tmp") == 0


def test_web_display_dashboard_interactions() -> None:
    _need_chromium()
    assert run_dashboard_interactions() == 0


def test_harness_server_closes_on_error() -> None:
    port = 0
    with pytest.raises(RuntimeError, match="forced"):
        with harness_server() as server:
            port = int(server.server_address[1])
            assert server.thread.is_alive()
            raise RuntimeError("forced")
    assert port > 0
    assert not server.thread.is_alive()
    probe = socket.socket()
    try:
        probe.settimeout(0.4)
        with pytest.raises(OSError):
            probe.connect(("127.0.0.1", port))
    finally:
        probe.close()


def test_no_refresh_guard_detects_injected_post() -> None:
    with harness_server() as server:
        failures: list[str] = []
        before = refresh_posts_in(server)
        assert_no_extra_refresh(server, before, failures, "baseline")
        assert failures == []
        # A GET with the same path must not be treated as a refresh.
        server.api_log.append(("GET", "/api/v1/commands/manual-refresh"))
        assert_no_extra_refresh(server, before, failures, "get-only")
        assert failures == []
        # Injected POST must fail the guard; this is the leak the previous
        # api_gets assertion could not catch.
        server.api_log.append(("POST", "/api/v1/commands/manual-refresh"))
        assert_no_extra_refresh(server, before, failures, "injected")
        assert any("extra refresh POST" in item for item in failures)
