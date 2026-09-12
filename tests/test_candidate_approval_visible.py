"""Official-page hit-target checks for the opt-in approval control."""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from playwright.sync_api import Page, sync_playwright
from test_candidate_approval_app import make_app

from stock_watcher.feedback.schema import database, install_on_connection


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture()
def live_origin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    port = _free_port()
    origin = f"http://127.0.0.1:{port}"
    monkeypatch.setenv("STOCKWATCHER_CANDIDATE_APPROVALS", "1")
    app = make_app(tmp_path, monkeypatch, enabled=True)
    object.__setattr__(app.state.settings, "public_origin", origin)
    with database(app.state.settings.db_path, write=True) as connection:
        install_on_connection(connection)
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None)
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline and not server.started:
        time.sleep(0.05)
    if not server.started:
        raise RuntimeError("approval visible-control server failed to start")
    try:
        yield origin
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def _login(page: Page, origin: str) -> None:
    page.goto(origin + "/", wait_until="networkidle")
    page.fill("#username", "approval-a")
    page.fill("#password", "isolated-approval-test")
    page.click("#login-form button[type='submit']")
    page.wait_for_function(
        """() => {
          const boxes = [...document.querySelectorAll('[data-approval-checkbox]')];
          return boxes.length === 3 && boxes.every((el) => !el.disabled);
        }""",
        timeout=10000,
    )


def _hit(page: Page) -> dict[str, object]:
    box = page.locator("[data-approval-checkbox]").first
    box.evaluate("el => el.scrollIntoView({block:'center'})")
    rect = box.bounding_box()
    assert rect
    result = page.evaluate(
        """([x, y]) => {
          const el = document.elementFromPoint(x, y);
          return {
            tag: el && el.tagName,
            isApproval: !!(el && el.closest('[data-approval-control]')),
            isDetail: !!(el && el.closest('.card-open-detail')),
          };
        }""",
        [rect["x"] + rect["width"] / 2, rect["y"] + rect["height"] / 2],
    )
    assert isinstance(result, dict)
    return result


@pytest.mark.skipif(
    __import__("os").environ.get("STOCKWATCHER_REQUIRE_UI") != "1"
    and not Path("/Users/kahlilhazel/Library/Caches/ms-playwright").exists(),
    reason="Playwright Chromium required for official-page hit tests",
)
def test_detail_overlay_intercepts_without_fix_and_click_saves_with_fix(
    live_origin: str,
) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 820})
        _login(page, live_origin)
        styles = page.evaluate(
            """() => [...document.querySelectorAll('link[rel=stylesheet]')].map(
              (el) => new URL(el.href).pathname
            )"""
        )
        for required in (
            "/static/app.css",
            "/static/refinements.css",
            "/static/display.css",
            "/static/motion.css",
            "/static/candidate-approvals.css",
        ):
            assert required in styles
        def without_fix(route: Any) -> None:
            css = Path(
                "src/stock_watcher/server/static/candidate-approvals.css"
            ).read_text(encoding="utf-8")
            css = css.replace(
                "#cards[data-approvals-enabled=\"true\"] .card-open-detail::after {\n"
                "  pointer-events: none;\n"
                "}\n",
                "",
            )
            route.fulfill(status=200, content_type="text/css", body=css)

        page.route("**/candidate-approvals.css*", without_fix)
        page.reload(wait_until="networkidle")
        page.wait_for_function(
            """() => document.querySelectorAll('[data-approval-checkbox]').length === 3"""
        )
        blocked = _hit(page)
        assert blocked["isDetail"] is True
        page.unroute("**/candidate-approvals.css*")
        page.reload(wait_until="networkidle")
        page.wait_for_function(
            """() => {
              const boxes = [...document.querySelectorAll('[data-approval-checkbox]')];
              return boxes.length === 3 && boxes.every((el) => !el.disabled);
            }"""
        )
        restored = _hit(page)
        assert restored["isApproval"] is True
        assert restored["isDetail"] is False
        rect = page.locator("[data-approval-checkbox]").first.bounding_box()
        assert rect
        with page.expect_response(
            lambda resp: resp.request.method == "PUT"
            and "/api/v1/me/candidate-approvals/" in resp.url
            and resp.ok
        ):
            page.mouse.click(rect["x"] + rect["width"] / 2, rect["y"] + rect["height"] / 2)
        page.wait_for_function(
            """() => {
              const control = document.querySelector('[data-approval-control]');
              const label = document.querySelector('[data-approval-label]');
              return control?.dataset.approvalSelected === 'true'
                && label?.textContent === '选择';
            }"""
        )
        assert page.evaluate("() => !document.getElementById('drawer-overlay')?.open")
        browser.close()
