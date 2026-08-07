"""Fixture parity: final exporter Top20 contract and replay equivalence.

PAR-001: the final exporter emits exactly 20 ranked rows per scan and equals
the deterministic ``raw-top20-reconstructed`` projection.
PAR-002: the stable Top3 timeline export matches the baseline fixture.
PAR-004: automation planner obligations match the baseline fixture tasks.
PAR-006: local-fallback PDF manifest carries mode/renderer/source SHA/commit.
"""
from __future__ import annotations

import csv
import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path

from stock_watcher.domain import SHANGHAI
from stock_watcher.storage import SQLiteStore

HANDOFF_ROOT = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "reference"
    / "StockWatcher-Web-Internal-Test-Handoff-20260807"
)
# Prefer the handoff package location (downloads); fall back to the vendored
# contracts directory. The 13MB fixtures are not duplicated into the repo.
_DOWNLOAD_HANDOFF = (
    Path.home()
    / "Downloads"
    / "StockWatcher-Web-Internal-Test-Handoff-20260807"
    / "StockWatcher-Web-Internal-Test-Handoff-20260807"
)
FIXTURES = (
    _DOWNLOAD_HANDOFF / "baseline" / "fixtures"
    if (_DOWNLOAD_HANDOFF / "baseline" / "fixtures" / "raw-top20-reconstructed.json").is_file()
    else HANDOFF_ROOT / "baseline" / "fixtures"
)


def _fixtures_available() -> bool:
    return (FIXTURES / "raw-top20-reconstructed.json").is_file()


def _load(name: str) -> list[dict[str, object]]:
    loaded = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    assert isinstance(loaded, list)
    return loaded


def _load_csv(name: str) -> list[dict[str, str]]:
    with (FIXTURES / name).open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _build_replay_db(tmp_path: Path) -> SQLiteStore:
    """Load fixture scan evidence into a fresh v7 database and run the
    final exporter against it, proving the audit persistence -> export path
    reproduces the reconstructed baseline."""
    store = SQLiteStore(tmp_path / "replay.sqlite3")
    store.initialize()
    scans = _load("scan-runs.json")
    audit_rows = _load("candidate-audit.json")
    stable_timeline = _load("stable-top3-timeline.json")
    by_scan: dict[str, list[dict[str, object]]] = {}
    for row in audit_rows:
        by_scan.setdefault(str(row["scan_id"]), []).append(row)
    stable_by_scan: dict[str, list[dict[str, object]]] = {}
    for row in stable_timeline:
        stable_by_scan.setdefault(str(row["scan_id"]), []).append(row)
    with store.transaction() as connection:
        for scan in scans:
            scan_id = str(scan["id"])
            audit = {
                "warmup_state": "ready",
                "raw_codes": [
                    row["code"]
                    for row in sorted(
                        by_scan.get(scan_id, []),
                        key=lambda row: (row.get("selected_raw") != "True", row["raw_rank"]),
                    )
                    if row.get("selected_raw") == "True"
                ][:3],
                "stable_codes": [row["code"] for row in stable_by_scan.get(scan_id, [])],
                "rows": [
                    {key: value for key, value in row.items()}
                    for row in by_scan.get(scan_id, [])
                ],
            }
            connection.execute(
                "INSERT INTO scan_runs (id, started_at, completed_at, trigger_type, "
                "task_key, health, source_ts, coverage_ratio, elapsed_seconds, "
                "source_age_seconds, detail, raw_batch_json, stable_batch_json, "
                "audit_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    scan["id"],
                    scan["started_at"],
                    scan["completed_at"],
                    scan["trigger_type"],
                    scan.get("task_key"),
                    scan["health"],
                    scan.get("source_ts"),
                    scan.get("coverage_ratio"),
                    scan.get("elapsed_seconds"),
                    scan.get("source_age_seconds"),
                    scan.get("detail", ""),
                    scan.get("raw_batch_json"),
                    scan.get("stable_batch_json"),
                    json.dumps(audit, ensure_ascii=False, sort_keys=True),
                ),
            )
    return store


def _run_exporter(db: Path, trade_date: str, output: Path) -> None:
    script = (
        Path(__file__).resolve().parents[1] / "scripts" / "export_selection_audit.py"
    )
    spec = importlib.util.spec_from_file_location("replay_exporter", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["replay_exporter"] = module
    spec.loader.exec_module(module)
    old_argv = sys.argv
    sys.argv = ["export", str(db), trade_date, str(output)]
    try:
        assert module.main() == 0
    finally:
        sys.argv = old_argv


def test_exporter_true_top20_equals_reconstructed(tmp_path: Path) -> None:
    if not _fixtures_available():
        import pytest

        pytest.skip("handoff fixtures not vendored")
    store = _build_replay_db(tmp_path)
    output = tmp_path / "export"
    _run_exporter(store.path, "2026-08-06", output)
    exported = json.loads((output / "raw-top20.json").read_text(encoding="utf-8"))
    reconstructed = _load("raw-top20-reconstructed.json")
    # Normalize field subsets: exported adds fields; compare the contract keys.
    keys = ("scan_id", "rank", "code", "name", "sector", "level")
    exported_projection = [
        {key: str(row.get(key, "")) for key in keys} for row in exported
    ]
    reconstructed_projection = [
        {key: str(row.get(key, "")) for key in keys} for row in reconstructed
    ]
    assert exported_projection == reconstructed_projection
    by_scan: dict[str, int] = {}
    for row in exported:
        by_scan[str(row["scan_id"])] = by_scan.get(str(row["scan_id"]), 0) + 1
    assert all(count == 20 for count in by_scan.values()), by_scan


def test_exporter_stable_timeline_matches_fixture(tmp_path: Path) -> None:
    if not _fixtures_available():
        import pytest

        pytest.skip("handoff fixtures not vendored")
    store = _build_replay_db(tmp_path)
    output = tmp_path / "export"
    _run_exporter(store.path, "2026-08-06", output)
    exported = _load_csv(str(output / "stable-top3-timeline.csv"))
    fixture = _load("stable-top3-timeline.json")
    keys = ("scan_id", "rank", "code")
    exported_projection = [
        {key: str(row.get(key, "")) for key in keys} for row in exported
    ]
    fixture_projection = [
        {key: str(row.get(key, "")) for key in keys} for row in fixture
    ]
    assert exported_projection == fixture_projection


def test_exporter_raw_top3_explicit_three_rows(tmp_path: Path) -> None:
    if not _fixtures_available():
        import pytest

        pytest.skip("handoff fixtures not vendored")
    store = _build_replay_db(tmp_path)
    output = tmp_path / "export"
    _run_exporter(store.path, "2026-08-06", output)
    top3 = json.loads((output / "raw-top3.json").read_text(encoding="utf-8"))
    assert len(top3) == 415 * 3
    by_scan: dict[str, int] = {}
    for row in top3:
        by_scan[str(row["scan_id"])] = by_scan.get(str(row["scan_id"]), 0) + 1
    assert all(count == 3 for count in by_scan.values())


def test_automation_planner_parity() -> None:
    """AutomationPlanner (unchanged baseline code) reproduces fixture tasks."""
    if not _fixtures_available():
        import pytest

        pytest.skip("handoff fixtures not vendored")
    from stock_watcher.runtime import AutomationPlanner

    fixture = _load("automation-tasks.json")
    trade_date = datetime(2026, 8, 6).date()
    planner = AutomationPlanner()
    specs = {spec.task_key: spec for spec in planner.for_date(trade_date)}
    for task in fixture:
        key = str(task["task_key"])
        assert key in specs, f"fixture task {key} not planned"
        spec = specs[key]
        assert task["task_type"] == spec.task_type.value
        assert task["target_at"] == spec.target_at.isoformat()
        assert task["deadline_at"] == spec.deadline_at.isoformat()
    assert {t["task_type"] for t in fixture} == {
        "scheduled-09:45",
        "scheduled-14:45",
        "summary-15:30",
    }


def test_local_summary_pdf_manifest_contract(tmp_path: Path) -> None:
    """PAR-006: local fallback PDF manifest carries the contract fields and no
    forbidden placeholder text."""
    if not _fixtures_available():
        import pytest

        pytest.skip("handoff fixtures not vendored")
    from stock_watcher.engine import DailySummaryEngine
    from stock_watcher.runtime import write_local_fallback_artifacts
    from stock_watcher.storage import SQLiteStore

    store = SQLiteStore(tmp_path / "pdf.sqlite3")
    store.initialize()
    now = datetime(2026, 8, 6, 15, 30, tzinfo=SHANGHAI)
    summary = (
        DailySummaryEngine()
        .generate(
            trade_date=now.date(),
            generated_at=now,
            alert_history=[],
            observation_history=[{"payload_json": '{"candidates": []}'}],
            health_interruption_count=0,
            continuity_evidence="未记录到扫描或连续性事件。",
            catch_up=False,
            version="daily-summary-local-fallback-v1",
        )
        .as_record()
    )
    reports = tmp_path / "reports"
    reports.mkdir()
    write_local_fallback_artifacts(
        store,
        summary,
        reports_dir=reports,
        now=now,
        source_commit_value="502a447d7e593d638ea45518f2a5e4d4827f683f",
    )
    pdf = reports / "2026-08-06-A股盘后回顾.pdf"
    meta = reports / "2026-08-06-A股盘后回顾.pdf.meta.json"
    local_json = reports / "2026-08-06-local-summary.json"
    assert pdf.is_file() and pdf.stat().st_size > 0
    assert meta.is_file() and local_json.is_file()
    manifest = json.loads(meta.read_text(encoding="utf-8"))
    required_fields = (
        "report_mode",
        "renderer_version",
        "source_version",
        "source_sha256",
        "source_commit",
    )
    for field in required_fields:
        assert field in manifest, f"manifest missing {field}"
    assert manifest["report_mode"] in {"full_market", "local_fallback"}
    pdf_bytes = pdf.read_bytes()
    assert "15:30总结running".encode() not in pdf_bytes
