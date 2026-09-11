"""Run display-density capture checks as a pytest regression."""

from __future__ import annotations

import runpy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CAPTURE = ROOT / "evidence" / "web-display-density" / "capture.py"


def test_web_display_capture_script() -> None:
    ns = runpy.run_path(str(CAPTURE), run_name="not_main")
    generate = ns["generate_evidence"]
    try:
        code = generate()
    except Exception as exc:
        message = str(exc)
        if "Executable doesn't exist" in message or "playwright install" in message:
            pytest.skip("Playwright Chromium is not installed in this environment")
        raise
    assert code == 0
