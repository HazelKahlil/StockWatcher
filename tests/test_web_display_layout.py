"""Display-density UI regression. Does not overwrite committed evidence."""

from __future__ import annotations

from pathlib import Path

import pytest
from web_display_harness import (
    chromium_available,
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
