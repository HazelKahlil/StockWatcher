"""Shared display-density browser harness. Writes evidence only when asked."""

from __future__ import annotations

import hashlib
import http.client
import json
import mimetypes
import os
import platform
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, urlparse

from jinja2 import Environment, FileSystemLoader
from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_DIR = ROOT / "src" / "stock_watcher" / "server" / "templates"
STATIC_DIR = ROOT / "src" / "stock_watcher" / "server" / "static"
MAX_WIDE_GAP = 96
SOURCE_FILES = (
    "src/stock_watcher/server/static/display.css",
    "src/stock_watcher/server/static/display.js",
    "src/stock_watcher/server/static/candidate-card.js",
    "src/stock_watcher/server/templates/dashboard.html",
    "src/stock_watcher/server/templates/base.html",
)

CANDIDATES = (
    {
        "rank": 1,
        "code": "300829.SZ",
        "name": "金丹科技",
        "level": "强",
        "is_formal": True,
        "price": 18.46,
        "change_pct": 6.82,
        "sector_name": "生物制品",
        "explanation": "模拟详情：用于焦点与抽屉回归。",
    },
    {
        "rank": 2,
        "code": "300741.SZ",
        "name": "华宝股份",
        "level": "中",
        "is_formal": True,
        "price": 22.08,
        "change_pct": 4.15,
        "sector_name": "食品饮料",
        "explanation": "模拟详情 2。",
    },
    {
        "rank": 3,
        "code": "300106.SZ",
        "name": "西部牧业",
        "level": "近",
        "is_formal": False,
        "price": 7.93,
        "change_pct": -1.24,
        "sector_name": "农产品加工",
        "explanation": "模拟详情 3。",
    },
)

STRESS_CANDIDATES = (
    {
        **CANDIDATES[0],
        "name": "内蒙古包钢稀土高科技股份",
        "change_pct": 129.99,
        "sector_name": "稀土永磁材料概念",
    },
    CANDIDATES[1],
    CANDIDATES[2],
)

MEASURE_JS = """
() => {
  const box = (r) => ({x: r.left, y: r.top, w: r.width, h: r.height});
  const textBoxes = (el) => {
    if (!el) return [];
    const range = document.createRange();
    range.selectNodeContents(el);
    return [...range.getClientRects()]
      .filter((r) => r.width && r.height)
      .map(box);
  };
  const hit = (a, b, pad) => (
    a.x + a.w > b.x + pad && b.x + b.w > a.x + pad
    && a.y + a.h > b.y + pad && b.y + b.h > a.y + pad
  );
  const cards = [...document.querySelectorAll('.dashboard-cards > .card')];
  return {
    viewport: [window.innerWidth, window.innerHeight],
    scale: document.documentElement.dataset.uiScale,
    layout: document.documentElement.dataset.watchLayout,
    mainW: Math.round(
      document.getElementById('main').getBoundingClientRect().width
    ),
    overflow: document.documentElement.scrollWidth
      > document.documentElement.clientWidth + 2,
    cards: cards.map((card, index) => {
      const name = card.querySelector('.display-name');
      const code = card.querySelector('.display-code');
      const pct = card.querySelector('.ashare-pct');
      const primary = card.querySelector(
        '.candidate-primary, .candidate-main'
      );
      const quote = card.querySelector('.candidate-quote');
      const nameBoxes = textBoxes(name);
      const pctBoxes = textBoxes(pct);
      const codeBox = box(code.getBoundingClientRect());
      const nameRight = Math.max(0, ...nameBoxes.map((b) => b.x + b.w));
      const pctLeft = Math.min(...pctBoxes.map((b) => b.x), Infinity);
      return {
        index,
        waiting: card.classList.contains('placeholder-card'),
        card: [
          Math.round(card.getBoundingClientRect().width),
          Math.round(card.getBoundingClientRect().height),
        ],
        primaryW: Math.round(primary.getBoundingClientRect().width),
        nameText: (name.textContent || '').trim(),
        nameLines: nameBoxes.length,
        codeText: (code.textContent || '').trim(),
        codeClip: code.scrollWidth > code.clientWidth + 1,
        pctText: (pct.textContent || '').trim(),
        quoteLeft: Math.round(
          quote.getBoundingClientRect().left
          - card.getBoundingClientRect().left
        ),
        nameToQuoteGap: Math.round(pctLeft - nameRight),
        nameQuoteOverlap: nameBoxes.some(
          (nb) => pctBoxes.some((pb) => hit(nb, pb, 1))
        ),
        codeQuoteOverlap: pctBoxes.some((pb) => hit(codeBox, pb, 1)),
      };
    }),
  };
}
"""


def source_hashes() -> dict[str, str]:
    result: dict[str, str] = {}
    for relative in SOURCE_FILES:
        payload = (ROOT / relative).read_bytes()
        result[relative] = hashlib.sha256(payload).hexdigest()[:12]
    return result


def git_head() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        text=True,
    ).strip()


def render_page(name: str) -> bytes:
    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)))
    user = SimpleNamespace(username="preview", role="user")
    html = env.get_template(name).render(
        user=user,
        csrf="test-csrf",
        product_version="ui-test",
    )
    return html.encode("utf-8")


def public_state(stress: bool = False, empty: bool = False) -> dict[str, Any]:
    if empty:
        rows: tuple[dict[str, Any], ...] = ()
    elif stress:
        rows = STRESS_CANDIDATES
    else:
        rows = CANDIDATES
    return {
        "service_state": "healthy",
        "market_state": "afternoon",
        "snapshot_id": 42,
        "source_ts": "2026-09-10T14:30:57+08:00",
        "candidates_source": "current_public_state",
        "worker_heartbeat_age_seconds": 3,
        "fund_module": "unavailable",
        "overall_weak": False,
        "last_scan": {"completed_at": "2026-09-10T14:30:58+08:00"},
        "tasks": [
            {"task_type": "morning_scan", "state": "completed"},
            {"task_type": "afternoon_scan", "state": "pending"},
        ],
        "candidates": list(rows),
    }


READY_JS = """
(expected) => {
  const html = document.documentElement;
  if (expected.scale && html.dataset.uiScale !== String(expected.scale)) {
    return false;
  }
  if (expected.layout && html.dataset.watchLayout !== expected.layout) {
    return false;
  }
  const pref = expected.pref || (expected.skipCards ? null : expected.layout);
  if (pref && html.dataset.watchPref !== String(pref)) {
    return false;
  }
  const label = document.getElementById('display-scale-label');
  if (expected.scale && label
      && label.textContent.trim() !== `${expected.scale}%`) {
    return false;
  }
  const slider = document.getElementById('display-scale');
  if (expected.scale && slider
      && String(slider.value) !== String(expected.scale)) {
    return false;
  }
  const compact = document.getElementById('display-compact');
  if (pref && compact) {
    const pressed = compact.getAttribute('aria-pressed') === 'true';
    if (pressed !== (pref === 'compact')) return false;
  }
  if (expected.skipCards) return true;
  const cards = [...document.querySelectorAll('.dashboard-cards > .card')];
  if (cards.length !== 3) return false;
  const waiting = cards.every((card) => (
    card.classList.contains('placeholder-card')
  ));
  if (expected.waiting) {
    if (!waiting) return false;
    return cards.every((card) => {
      const name = (
        card.querySelector('.display-name')?.textContent || ''
      ).trim();
      const code = (
        card.querySelector('.display-code')?.textContent || ''
      ).trim();
      return name === '等待候选' && code === '------';
    });
  }
  if (waiting) return false;
  if (expected.codes) {
    const codes = cards.map((card) => (
      (card.querySelector('.display-code')?.textContent || '').trim()
    ));
    if (codes.join('|') !== expected.codes.join('|')) return false;
  }
  if (expected.name) {
    const name = (
      cards[0].querySelector('.display-name')?.textContent || ''
    ).trim();
    if (name !== expected.name) return false;
  }
  if (expected.pct) {
    const pct = (
      cards[0].querySelector('.ashare-pct')?.textContent || ''
    ).trim();
    if (pct !== expected.pct) return false;
  }
  return true;
}
"""

NORMAL_CODES = [row["code"] for row in CANDIDATES]


class HarnessServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    state: dict[str, Any]
    refresh_posts: int
    api_log: list[tuple[str, str]]
    thread: threading.Thread
    _closed: bool

    def __init__(
        self,
        server_address: tuple[str, int],
        handler: type[BaseHTTPRequestHandler],
    ) -> None:
        super().__init__(server_address, handler)
        self.state = public_state()
        self.refresh_posts = 0
        self.api_log = []
        self._closed = False
        self.thread = threading.Thread(
            target=self._serve,
            name="display-harness",
            daemon=True,
        )

    def _serve(self) -> None:
        self.serve_forever(poll_interval=0.05)

    def start(self) -> None:
        self.thread.start()
        self._wait_until_serving()

    def _wait_until_serving(self) -> None:
        # serve_forever must already be in its loop before shutdown() is safe.
        deadline = time.monotonic() + 5
        last_error: Exception | None = None
        port = int(self.server_address[1])
        while time.monotonic() < deadline:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=0.3)
            try:
                conn.request("GET", "/harness/ready")
                response = conn.getresponse()
                response.read()
                if response.status == 200:
                    return
                last_error = RuntimeError(f"unexpected status {response.status}")
            except OSError as exc:
                last_error = exc
            finally:
                conn.close()
            time.sleep(0.02)
        raise RuntimeError(f"display harness failed to start: {last_error}")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        # shutdown() blocks until serve_forever returns; never call it from the
        # server thread. A helper thread keeps a stuck shutdown from hanging tests.
        if self.thread.is_alive():
            stopper = threading.Thread(
                target=self.shutdown,
                name="display-harness-stop",
                daemon=True,
            )
            stopper.start()
            stopper.join(timeout=5)
            self.thread.join(timeout=5)
        self.server_close()

    def __enter__(self) -> HarnessServer:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def refresh_posts_in(server: HarnessServer) -> int:
    return sum(
        1
        for method, path in server.api_log
        if method == "POST" and path == "/api/v1/commands/manual-refresh"
    )


def assert_no_extra_refresh(
    server: HarnessServer,
    before: int,
    failures: list[str],
    label: str,
) -> None:
    extra = refresh_posts_in(server) - before
    if extra:
        failures.append(f"{label}: extra refresh POST x{extra}")


class HarnessHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        server = self.server
        assert isinstance(server, HarnessServer)
        if path == "/harness/ready":
            self._send(200, "text/plain", b"ok")
            return
        if path in {"/", "/dashboard"}:
            self._send(200, "text/html; charset=utf-8", render_page("dashboard.html"))
            return
        if path == "/history":
            self._send(200, "text/html; charset=utf-8", render_page("history.html"))
            return
        if path.startswith("/static/"):
            self._send_static(path[len("/static/") :])
            return
        if path == "/api/v1/state":
            server.api_log.append(("GET", path))
            self._send_json(server.state)
            return
        if path.startswith("/api/v1/outcomes"):
            server.api_log.append(("GET", path))
            self._send_json(
                {
                    "summary": {
                        "win_rate": 0.293,
                        "average_return_pct": -1.53,
                        "settled_count": 75,
                        "total_count": 96,
                    },
                    "portfolio": {"win_rate": 0.273},
                    "backfill": {"message": "模拟回补完成。"},
                }
            )
            return
        if path.startswith("/api/v1/candidates/"):
            server.api_log.append(("GET", path))
            code = path.rsplit("/", 1)[-1]
            query = parse_qs(parsed.query)
            snapshot = (query.get("snapshot_id") or ["42"])[0]
            match = next(
                (row for row in server.state["candidates"] if row["code"] == code),
                None,
            )
            if match is None:
                self._send(404, "application/json", b'{"error":{"message":"not_found"}}')
                return
            self._send_json(
                {
                    "snapshot_id": int(snapshot),
                    "source_ts": server.state["source_ts"],
                    "candidate": match,
                }
            )
            return
        if path.startswith("/api/v1/commands/"):
            server.api_log.append(("GET", path))
            self._send_json({"command_id": "cmd-1", "status": "succeeded"})
            return
        self._send(404, "text/plain", b"not found")

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        server = self.server
        assert isinstance(server, HarnessServer)
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        if parsed.path == "/api/v1/commands/manual-refresh":
            server.api_log.append(("POST", parsed.path))
            server.refresh_posts += 1
            self._send_json({"command_id": "cmd-1", "status": "queued"})
            return
        self._send(404, "text/plain", b"not found")

    def _send_static(self, relative: str) -> None:
        target = (STATIC_DIR / relative).resolve()
        if not str(target).startswith(str(STATIC_DIR.resolve())) or not target.is_file():
            self._send(404, "text/plain", b"missing")
            return
        mime, _ = mimetypes.guess_type(str(target))
        self._send(200, mime or "application/octet-stream", target.read_bytes())

    def _send_json(self, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self._send(200, "application/json", body)

    def _send(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def harness_server(state: dict[str, Any] | None = None) -> HarnessServer:
    server = HarnessServer(("127.0.0.1", 0), HarnessHandler)
    server.state = state or public_state()
    return server


def wait_ready(page: Page, expected: dict[str, Any]) -> None:
    page.wait_for_function(READY_JS, arg=expected)


def apply_prefs(page: Page, scale: str, layout: str, ready: dict[str, Any]) -> None:
    page.evaluate(
        """([scale, layout]) => {
          localStorage.setItem('stockwatcher.ui.scale', scale);
          localStorage.setItem('stockwatcher.ui.watchLayout', layout);
        }""",
        [scale, layout],
    )
    page.reload(wait_until="networkidle")
    wait_ready(page, ready)


def check_geometry(payload: dict[str, Any], label: str, failures: list[str]) -> None:
    if payload["overflow"]:
        failures.append(f"{label}: page overflow")
    for card in payload["cards"]:
        tag = f"{label}#{int(card['index']) + 1}"
        if card["nameQuoteOverlap"]:
            failures.append(f"{tag}: name overlaps quote")
        if card["codeQuoteOverlap"]:
            failures.append(f"{tag}: code overlaps quote")
        if card["codeClip"]:
            failures.append(f"{tag}: code clipped ({card['codeText']})")
        name = str(card["nameText"])
        waiting = bool(card["waiting"])
        if not waiting and len(name) <= 6 and int(card["nameLines"]) >= 3:
            failures.append(f"{tag}: short name stacked ({name})")
        wide = int(payload["mainW"]) >= 900
        if wide and not waiting and len(name) <= 6:
            gap = int(card["nameToQuoteGap"])
            if gap > MAX_WIDE_GAP:
                failures.append(f"{tag}: name-quote gap {gap}px")


def run_geometry(
    out_dir: Path | None = None,
    write_shots: bool = False,
    prefix: str = "round3",
) -> int:
    failures: list[str] = []
    shots: list[str] = []
    rows: list[dict[str, Any]] = []
    normal = {
        "codes": NORMAL_CODES,
        "waiting": False,
        "scale": "100",
        "layout": "full",
    }
    with harness_server() as server:
        origin = f"http://127.0.0.1:{server.server_address[1]}"
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": 1280, "height": 820})
                page.goto(origin + "/", wait_until="networkidle")
                wait_ready(page, normal)

                def record(
                    label: str,
                    shot: str | None = None,
                ) -> dict[str, Any]:
                    data = page.evaluate(MEASURE_JS)
                    assert isinstance(data, dict)
                    data["label"] = label
                    check_geometry(data, label, failures)
                    rows.append(data)
                    if write_shots and shot and out_dir is not None:
                        page.screenshot(
                            path=str(out_dir / f"{shot}.png"),
                            full_page=True,
                        )
                        shots.append(shot)
                    return data

                apply_prefs(page, "100", "full", normal)
                record("1280-100-full", f"{prefix}-100-desktop")
                before = refresh_posts_in(server)
                page.click("#display-toggle")
                page.fill("#display-scale", "35")
                page.dispatch_event("#display-scale", "input")
                wait_ready(
                    page,
                    {**normal, "scale": "35", "layout": "full"},
                )
                page.click("#display-panel-close")
                record("1280-35-full", f"{prefix}-35-full")
                assert_no_extra_refresh(
                    server,
                    before,
                    failures,
                    "scale to 35%",
                )
                before = refresh_posts_in(server)
                page.click("#display-toggle")
                page.click("#display-compact")
                wait_ready(
                    page,
                    {**normal, "scale": "35", "layout": "compact"},
                )
                page.click("#display-panel-close")
                record("1280-35-compact", f"{prefix}-35-compact")
                assert_no_extra_refresh(
                    server,
                    before,
                    failures,
                    "compact toggle",
                )
                before = refresh_posts_in(server)
                page.click("#display-toggle")
                page.fill("#display-scale", "20")
                page.dispatch_event("#display-scale", "input")
                wait_ready(
                    page,
                    {**normal, "scale": "20", "layout": "compact"},
                )
                page.click("#display-panel-close")
                record("1280-20-compact", f"{prefix}-20-compact")
                assert_no_extra_refresh(
                    server,
                    before,
                    failures,
                    "scale to 20%",
                )

                page.set_viewport_size({"width": 430, "height": 860})
                apply_prefs(page, "100", "full", normal)
                record("430-100-full", f"{prefix}-100-narrow")

                server.state = public_state(stress=True)
                page.set_viewport_size({"width": 390, "height": 720})
                apply_prefs(
                    page,
                    "150",
                    "full",
                    {
                        "scale": "150",
                        "layout": "full",
                        "waiting": False,
                        "name": "内蒙古包钢稀土高科技股份",
                        "pct": "+129.99%",
                    },
                )
                record("390-150-stress", f"{prefix}-390-150-stress")

                server.state = public_state(empty=True)
                page.set_viewport_size({"width": 390, "height": 720})
                apply_prefs(
                    page,
                    "100",
                    "compact",
                    {
                        "scale": "100",
                        "layout": "compact",
                        "waiting": True,
                    },
                )
                record("390-100-empty-compact", f"{prefix}-waiting")

                page.set_viewport_size({"width": 1024, "height": 800})
                page.goto(origin + "/history", wait_until="networkidle")
                apply_prefs(
                    page,
                    "20",
                    "compact",
                    {
                        "scale": "20",
                        "layout": "full",
                        "pref": "compact",
                        "skipCards": True,
                    },
                )
                page.wait_for_selector("h1")
                hist_w = page.evaluate(
                    "Math.round(document.getElementById('main').getBoundingClientRect().width)"
                )
                if hist_w < 700:
                    failures.append(f"history column too narrow at 20%: {hist_w}")
                if "历史观察" not in page.inner_text("h1"):
                    failures.append("history heading hidden")
            finally:
                browser.close()
    if out_dir is not None:
        payload = {
            "tested_commit": git_head(),
            "browser": "chromium",
            "os": platform.platform(),
            "dataset": "harness-fixtures-v3",
            "source_hashes": source_hashes(),
            "shots": shots,
            "rows": rows,
            "failures": failures,
        }
        (out_dir / f"{prefix}-metrics.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    if failures:
        print("CAPTURE_FAILED")
        for item in failures:
            print(item)
        return 1
    print("CAPTURE_OK " + ",".join(shots))
    return 0


def run_dashboard_interactions() -> int:
    failures: list[str] = []
    normal = {
        "codes": NORMAL_CODES,
        "waiting": False,
        "scale": "100",
        "layout": "full",
    }
    with harness_server() as server:
        origin = f"http://127.0.0.1:{server.server_address[1]}"
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": 1280, "height": 820})
                page.goto(origin + "/", wait_until="networkidle")
                wait_ready(page, normal)
                before = refresh_posts_in(server)
                page.click("#display-toggle")
                page.fill("#display-scale", "70")
                page.dispatch_event("#display-scale", "input")
                wait_ready(page, {**normal, "scale": "70"})
                page.click("#display-panel-close")
                assert_no_extra_refresh(
                    server,
                    before,
                    failures,
                    "slider on live dashboard",
                )
                apply_prefs(page, "100", "full", normal)
                first = page.locator(".card-open-detail").first
                first.focus()
                with page.expect_response("**/api/v1/candidates/**"):
                    first.click()
                page.wait_for_selector("#drawer-overlay[open]")
                page.wait_for_selector("#detail .detail-stock-name, #detail h2")
                if "金丹科技" not in page.inner_text("#detail"):
                    failures.append("detail drawer missing stock name")
                page.click("#close-drawer-btn")
                page.wait_for_function("() => !document.getElementById('drawer-overlay').open")
                active = page.evaluate(
                    "document.activeElement && document.activeElement.getAttribute('data-detail')"
                )
                if active != "300829.SZ":
                    failures.append(f"focus did not return to detail button: {active}")
                before_refresh = refresh_posts_in(server)
                try:
                    with page.expect_request(
                        lambda req: (
                            req.method == "POST"
                            and req.url.endswith("/api/v1/commands/manual-refresh")
                        ),
                        timeout=5000,
                    ):
                        page.click("#manual-refresh")
                except Exception:
                    failures.append("refresh click did not POST manual-refresh")
                if refresh_posts_in(server) != before_refresh + 1:
                    failures.append("refresh POST count did not increase by 1")
            finally:
                browser.close()
    if failures:
        print("DASHBOARD_FAILED")
        for item in failures:
            print(item)
        return 1
    print("DASHBOARD_OK")
    return 0


def chromium_available() -> bool:
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            browser.close()
        return True
    except Exception as exc:
        message = str(exc)
        if "Executable doesn't exist" in message:
            return False
        raise


def require_ui() -> bool:
    return os.environ.get("STOCKWATCHER_REQUIRE_UI") == "1"
