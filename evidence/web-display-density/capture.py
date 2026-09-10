#!/usr/bin/env python3
"""Isolated visual capture for display size and compact watch. Mock data only."""

from __future__ import annotations

import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 8765), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    shots: list[str] = []

    def shot(page, name: str) -> None:
        page.screenshot(path=str(OUT / f"{name}.png"), full_page=True)
        shots.append(name)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 820})
        page.goto(
            "http://127.0.0.1:8765/evidence/web-display-density/preview-dashboard.html",
            wait_until="networkidle",
        )
        page.evaluate("localStorage.clear(); location.reload();")
        page.wait_for_selector("#display-toggle")
        page.wait_for_selector("#display-toggle")
        shot(page, "desktop-100")

        page.click("#display-toggle")
        page.wait_for_selector("#display-panel:not([hidden])")
        shot(page, "desktop-panel-open")

        page.fill("#display-scale", "80")
        page.dispatch_event("#display-scale", "input")
        label = page.inner_text("#display-scale-label").strip()
        if label != "80%":
            raise SystemExit(f"expected 80%, got {label}")
        page.click("#display-panel-close")
        shot(page, "desktop-80")

        page.click("#display-toggle")
        page.fill("#display-scale", "125")
        page.dispatch_event("#display-scale", "input")
        page.click("#display-panel-close")
        overflow_125 = page.evaluate(
            "document.documentElement.scrollWidth > document.documentElement.clientWidth + 2"
        )
        if overflow_125:
            raise SystemExit("125% horizontal overflow on desktop")
        shot(page, "desktop-125")
        page.click("#display-toggle")
        page.click("#display-reset")
        page.fill("#display-scale", "80")
        page.dispatch_event("#display-scale", "input")
        page.click("#display-panel-close")

        page.click("#display-toggle")
        page.click("#display-compact")
        page.wait_for_selector("#watch-exit:not([hidden])")
        intro_hidden = page.evaluate(
            "getComputedStyle(document.querySelector('.dashboard-intro')).display === 'none'"
        )
        outcome_hidden = page.evaluate(
            "getComputedStyle(document.querySelector('.outcome-editorial')).display === 'none'"
        )
        cards = page.locator(".dashboard-cards .card").count()
        if not intro_hidden or not outcome_hidden or cards != 3:
            raise SystemExit("compact layout did not keep three cards while hiding extras")
        page.click("#display-panel-close")
        page.set_viewport_size({"width": 420, "height": 720})
        shot(page, "compact-small-window")

        page.evaluate(
            """
            document.getElementById('svc-state').textContent = '陈旧';
            document.getElementById('svc-state').className = 'status-item-value pill-stale';
            document.getElementById('last-scan').textContent = '2026-09-10 14:23:53';
            const provenance = document.getElementById('candidate-provenance');
            provenance.dataset.retained = 'true';
            provenance.textContent = '保留快照 · 2026-09-10 14:23:53。当前未产生新的有效候选。';
            document.getElementById('ws-state').dataset.state = 'online';
            document.getElementById('ws-state').querySelector('.status-label').textContent = '实时连接在线';
            document.getElementById('top3-title').textContent = '上次 3 只观察 · 陈旧';
            """
        )
        shot(page, "compact-retained-snapshot")

        page.set_viewport_size({"width": 1280, "height": 820})
        page.goto(
            "http://127.0.0.1:8765/evidence/web-display-density/preview-history.html",
            wait_until="networkidle",
        )
        page.wait_for_selector("h1")
        heading = page.inner_text("h1")
        history_visible = page.evaluate(
            "getComputedStyle(document.querySelector('.page-heading')).display !== 'none'"
        )
        if not history_visible or "历史观察" not in heading:
            raise SystemExit("history page was hidden by compact preference")
        history_scale = page.inner_text("#display-scale-label").strip()
        if history_scale != "80%":
            raise SystemExit(f"history page lost scale preference: {history_scale}")
        page.click("#display-toggle")
        compact_visible = page.is_visible("#display-compact")
        if not compact_visible:
            raise SystemExit("compact toggle missing on history while preference is on")
        page.click("#display-panel-close")
        shot(page, "history-with-compact-pref")

        page.set_viewport_size({"width": 390, "height": 720})
        page.goto(
            "http://127.0.0.1:8765/evidence/web-display-density/preview-dashboard.html",
            wait_until="networkidle",
        )
        page.wait_for_selector("#display-toggle")
        overflow = page.evaluate(
            "document.documentElement.scrollWidth > document.documentElement.clientWidth + 2"
        )
        if overflow:
            raise SystemExit("390px horizontal overflow")
        shot(page, "mobile-390")

        page.evaluate(
            """
            document.getElementById('candidate-state').textContent = '等待数据';
            document.getElementById('last-scan').textContent = '尚无候选数据';
            document.getElementById('svc-state').textContent = '预热';
            document.getElementById('svc-state').className = 'status-item-value status-warming';
            document.getElementById('top3-title').textContent = '当前 0 只观察 · 预热';
            document.getElementById('candidate-provenance').textContent = '候选尚未就绪，等待有效扫描。';
            document.getElementById('candidate-provenance').dataset.retained = 'false';
            document.getElementById('cards').innerHTML = `
              <article class="card placeholder-card" aria-label="等待抓取第 1 只候选">
                <span class="rank rank-1-badge">1</span>
                <div class="candidate-identity"><h3 class="display-name placeholder-text">等待候选</h3><span class="display-code placeholder-text">------</span></div>
                <div class="candidate-quote"><span class="ashare-pct placeholder-text">--.--%</span><span class="ashare-price placeholder-text">¥--.--</span></div>
                <span class="level-tag level-placeholder">待</span>
                <div class="candidate-sector"><span class="candidate-meta-label">最强板块</span><strong class="placeholder-text">板块待抓取</strong></div>
              </article>
              <article class="card placeholder-card" aria-label="等待抓取第 2 只候选">
                <span class="rank rank-2-badge">2</span>
                <div class="candidate-identity"><h3 class="display-name placeholder-text">等待候选</h3><span class="display-code placeholder-text">------</span></div>
                <div class="candidate-quote"><span class="ashare-pct placeholder-text">--.--%</span><span class="ashare-price placeholder-text">¥--.--</span></div>
                <span class="level-tag level-placeholder">待</span>
                <div class="candidate-sector"><span class="candidate-meta-label">最强板块</span><strong class="placeholder-text">板块待抓取</strong></div>
              </article>
              <article class="card placeholder-card" aria-label="等待抓取第 3 只候选">
                <span class="rank rank-3-badge">3</span>
                <div class="candidate-identity"><h3 class="display-name placeholder-text">等待候选</h3><span class="display-code placeholder-text">------</span></div>
                <div class="candidate-quote"><span class="ashare-pct placeholder-text">--.--%</span><span class="ashare-price placeholder-text">¥--.--</span></div>
                <span class="level-tag level-placeholder">待</span>
                <div class="candidate-sector"><span class="candidate-meta-label">最强板块</span><strong class="placeholder-text">板块待抓取</strong></div>
              </article>`;
            """
        )
        shot(page, "compact-waiting-candidates")

        page.click("#display-toggle")
        page.focus("#display-scale")
        page.keyboard.press("ArrowRight")
        page.keyboard.press("Escape")
        if page.is_visible("#display-panel"):
            raise SystemExit("escape did not close display panel")

        browser.close()
    server.shutdown()
    print("CAPTURE_OK " + ",".join(shots))


if __name__ == "__main__":
    main()
