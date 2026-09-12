"""Official-page hit-target checks for the opt-in approval control."""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import uvicorn
from playwright.sync_api import Page, Route, sync_playwright
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
        css = Path(
            "src/stock_watcher/server/static/candidate-approvals.css"
        ).read_text(encoding="utf-8")
        assert "pointer-events: none" in css
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


@pytest.mark.skipif(
    __import__("os").environ.get("STOCKWATCHER_REQUIRE_UI") != "1"
    and not Path("/Users/kahlilhazel/Library/Caches/ms-playwright").exists(),
    reason="Playwright Chromium required for official-page hit tests",
)
def test_personal_state_recovers_after_503_without_reload(live_origin: str) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 820})
        fails = {"n": 1}

        def flaky(route: Route) -> None:
            if fails["n"] > 0:
                fails["n"] -= 1
                route.fulfill(
                    status=503,
                    content_type="application/json",
                    body='{"error":{"code":"feedback_unavailable"}}',
                )
                return
            route.continue_()

        page.route("**/api/v1/me/candidate-approvals/state**", flaky)
        page.goto(live_origin + "/", wait_until="networkidle")
        page.fill("#username", "approval-a")
        page.fill("#password", "isolated-approval-test")
        page.click("#login-form button[type='submit']")
        page.wait_for_function(
            """() => {
              const boxes = [...document.querySelectorAll('[data-approval-checkbox]')];
              return boxes.length === 3 && boxes.every((el) => !el.disabled);
            }""",
            timeout=8000,
        )
        browser.close()


@pytest.mark.skipif(
    __import__("os").environ.get("STOCKWATCHER_REQUIRE_UI") != "1"
    and not Path("/Users/kahlilhazel/Library/Caches/ms-playwright").exists(),
    reason="Playwright Chromium required for official-page hit tests",
)
def test_same_snapshot_refresh_keeps_switch_focus(live_origin: str) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 820})
        _login(page, live_origin)
        page.locator("[data-approval-checkbox]").nth(1).focus()
        assert page.evaluate(
            """() => document.activeElement?.matches('[data-approval-checkbox]')"""
        )
        page.evaluate(
            """async () => {
              const state = await (await fetch('/api/v1/state')).json();
              window.dispatchEvent(new CustomEvent(
                'stockwatcher:apply-dashboard-state',
                { detail: state },
              ));
            }"""
        )
        page.wait_for_function(
            """() => {
              const boxes = [...document.querySelectorAll('[data-approval-checkbox]')];
              return document.activeElement === boxes[1] && !boxes[1].disabled;
            }""",
            timeout=5000,
        )
        browser.close()
