#!/usr/bin/env python3
"""Capture display-density evidence and fail if layout checks fail."""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
PREVIEW = "/evidence/web-display-density/preview-dashboard.html"
DATASET = "preview-fixtures-v2"
SOURCE_FILES = (
    "src/stock_watcher/server/static/display.css",
    "src/stock_watcher/server/static/display.js",
    "src/stock_watcher/server/static/candidate-card.js",
    "src/stock_watcher/server/templates/dashboard.html",
    "src/stock_watcher/server/templates/base.html",
)

MEASURE_JS = """
() => {
  const card = document.querySelector('.dashboard-cards > .card');
  const name = card.querySelector('.display-name');
  const code = card.querySelector('.display-code');
  const quote = card.querySelector('.candidate-quote');
  const pct = card.querySelector('.ashare-pct');
  const primary = card.querySelector('.candidate-primary, .candidate-main');
  const r = card.getBoundingClientRect();
  const nr = name.getBoundingClientRect();
  const cr = code.getBoundingClientRect();
  const qr = quote.getBoundingClientRect();
  return {
    viewport: [window.innerWidth, window.innerHeight],
    scale: document.documentElement.dataset.uiScale,
    layout: document.documentElement.dataset.watchLayout,
    mainW: Math.round(document.getElementById('main').getBoundingClientRect().width),
    card: [Math.round(r.width), Math.round(r.height)],
    primary: [Math.round(primary.getBoundingClientRect().width),
              Math.round(primary.getBoundingClientRect().height)],
    name: [Math.round(nr.width), Math.round(nr.height),
           (name.textContent || '').trim()],
    nameRects: name.getClientRects().length,
    code: [Math.round(cr.width), Math.round(cr.height),
           (code.textContent || '').trim()],
    codeClip: code.scrollWidth > code.clientWidth + 1,
    quote: [Math.round(qr.width), Math.round(qr.left - r.left)],
    pct: (pct.textContent || '').trim(),
    overflow: document.documentElement.scrollWidth
      > document.documentElement.clientWidth + 2
  };
}
"""


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def log_message(self, format: str, *args: object) -> None:
        return


def source_hashes() -> dict[str, str]:
    result: dict[str, str] = {}
    for relative in SOURCE_FILES:
        payload = (ROOT / relative).read_bytes()
        result[relative] = hashlib.sha256(payload).hexdigest()[:12]
    return result


def git_head() -> str:
    output = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        text=True,
    )
    return output.strip()


def apply_prefs(page: Page, scale: str, layout: str, wait: str) -> None:
    page.evaluate(
        """([scale, layout]) => {
          localStorage.setItem('stockwatcher.ui.scale', scale);
          localStorage.setItem('stockwatcher.ui.watchLayout', layout);
          location.reload();
        }""",
        [scale, layout],
    )
    page.wait_for_selector(wait)


def assert_short_name_ok(row: dict[str, object], failures: list[str]) -> None:
    name = str(row["name"][2])  # type: ignore[index]
    height = int(row["name"][1])  # type: ignore[index]
    width = int(row["name"][0])  # type: ignore[index]
    rects = int(row["nameRects"])
    if len(name) <= 6 and (rects >= 3 or height > width * 1.8):
        failures.append(f"{row['label']}: short name stacked ({name})")
    if row["codeClip"]:
        failures.append(f"{row['label']}: code clipped ({row['code'][2]})")
    if row["overflow"]:
        failures.append(f"{row['label']}: horizontal overflow")
    pct = str(row["pct"])
    if pct and ("%" in pct) and ("\n" in pct):
        failures.append(f"{row['label']}: percent split")


def generate_evidence() -> int:
    failures: list[str] = []
    shots: list[str] = []
    rows: list[dict[str, object]] = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    origin = f"http://127.0.0.1:{port}"
    head = git_head()
    hashes = source_hashes()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        browser_name = "chromium"
        browser_ver = browser.version
        page = browser.new_page(viewport={"width": 1280, "height": 820})
        page.goto(origin + PREVIEW, wait_until="networkidle")
        requests: list[str] = []
        page.on(
            "request",
            lambda req: requests.append(req.url)
            if "/api/" in req.url
            else None,
        )

        def record(label: str, shot: str | None = None) -> dict[str, object]:
            page.wait_for_selector(".dashboard-cards > .card .display-name")
            data = page.evaluate(MEASURE_JS)
            data["label"] = label
            rows.append(data)
            if shot:
                page.screenshot(path=str(OUT / f"{shot}.png"), full_page=True)
                shots.append(shot)
            return data

        card_wait = ".dashboard-cards > .card .display-name"
        apply_prefs(page, "100", "full", card_wait)
        record("1280-100-full", "round2-100-desktop")
        before_api = len(requests)
        page.click("#display-toggle")
        page.wait_for_selector("#display-panel:not([hidden])")
        page.fill("#display-scale", "35")
        page.dispatch_event("#display-scale", "input")
        if page.inner_text("#display-scale-label").strip() != "35%":
            failures.append("slider did not apply 35%")
        page.click("#display-panel-close")
        record("1280-35-full", "round2-35-full")
        if len(requests) != before_api:
            failures.append("scale change issued extra API requests")

        page.click("#display-toggle")
        page.click("#display-compact")
        page.wait_for_selector("#watch-exit:not([hidden])")
        page.click("#display-panel-close")
        record("1280-35-compact", "round2-35-compact")

        page.click("#display-toggle")
        page.fill("#display-scale", "20")
        page.dispatch_event("#display-scale", "input")
        page.click("#display-panel-close")
        record("1280-20-compact", "round2-20-compact")

        page.click("#display-toggle")
        page.click("#display-reset")
        if page.inner_text("#display-scale-label").strip() != "100%":
            failures.append("reset did not restore 100%")
        page.click("[data-display-step='-5']")
        if page.inner_text("#display-scale-label").strip() != "95%":
            failures.append("minus step did not apply")
        page.keyboard.press("Escape")
        if page.is_visible("#display-panel"):
            failures.append("escape did not close panel")

        page = browser.new_page(viewport={"width": 430, "height": 860})
        page.goto(origin + PREVIEW, wait_until="networkidle")
        apply_prefs(page, "100", "full", card_wait)
        record("430-100-full", "round2-100-narrow")

        page = browser.new_page(viewport={"width": 390, "height": 720})
        page.goto(origin + PREVIEW + "?long=1", wait_until="networkidle")
        apply_prefs(page, "35", "full", card_wait)
        record("390-35-long", "round2-390-35-long")

        page.goto(origin + PREVIEW + "?empty=1", wait_until="networkidle")
        apply_prefs(page, "100", "compact", card_wait)
        record("390-100-empty-compact", "round2-waiting")

        page = browser.new_page(viewport={"width": 1280, "height": 820})
        page.add_init_script(
            "Storage.prototype.setItem = function () { throw new Error('quota'); };"
        )
        page.goto(origin + PREVIEW, wait_until="networkidle")
        page.click("#display-toggle")
        page.fill("#display-scale", "70")
        page.dispatch_event("#display-scale", "input")
        hint = page.inner_text("#display-panel-hint")
        if "未能保存" not in hint:
            failures.append("write failure hint missing")
        if page.inner_text("#display-scale-label").strip() != "70%":
            failures.append("write failure blocked live scale")

        matrix = (
            (320, 640, "100", "full"),
            (320, 640, "20", "compact"),
            (640, 800, "100", "full"),
            (768, 800, "70", "full"),
            (1024, 800, "100", "full"),
            (1280, 820, "25", "full"),
            (1280, 820, "150", "full"),
        )
        for width, height, scale, layout in matrix:
            page = browser.new_page(viewport={"width": width, "height": height})
            page.goto(origin + PREVIEW, wait_until="networkidle")
            apply_prefs(page, scale, layout, card_wait)
            record(f"{width}-{scale}-{layout}")

        page = browser.new_page(viewport={"width": 1024, "height": 800})
        page.goto(
            origin + "/evidence/web-display-density/preview-history.html",
            wait_until="networkidle",
        )
        apply_prefs(page, "20", "compact", "h1")
        hist_w = page.evaluate(
            "Math.round(document.getElementById('main').getBoundingClientRect().width)"
        )
        if hist_w < 700:
            failures.append(f"history column too narrow at 20%: {hist_w}")
        heading = page.inner_text("h1")
        if "历史观察" not in heading:
            failures.append("history heading hidden")

        browser.close()
    server.shutdown()

    for row in rows:
        label = str(row["label"])
        if "long" not in label and "empty" not in label:
            assert_short_name_ok(row, failures)

    payload = {
        "tested_commit": head,
        "browser": f"{browser_name} {browser_ver}",
        "os": platform.platform(),
        "dataset": DATASET,
        "source_hashes": hashes,
        "shots": shots,
        "rows": rows,
        "failures": failures,
    }
    (OUT / "round2-metrics.json").write_text(
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


if __name__ == "__main__":
    raise SystemExit(generate_evidence())
