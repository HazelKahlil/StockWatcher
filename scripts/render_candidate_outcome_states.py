from __future__ import annotations

import argparse
import os
import tempfile
import time
from datetime import date, datetime, timedelta
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from stock_watcher.domain import SHANGHAI, classify_return, return_pct  # noqa: E402
from stock_watcher.storage import SQLiteStore  # noqa: E402
from stock_watcher.ui.app import STYLE_SHEET  # noqa: E402
from stock_watcher.ui.history import HistoryDialog  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Render deterministic empty/pending/settled outcome review states."
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(STYLE_SHEET)
    with tempfile.TemporaryDirectory(prefix="stockwatcher-outcomes-") as raw_temp:
        temp = Path(raw_temp)
        for state in ("empty", "pending", "settled"):
            path = temp / f"{state}.sqlite3"
            store = SQLiteStore(path)
            store.initialize()
            if state != "empty":
                _seed(store, settled=state == "settled")
            dialog = HistoryDialog(path)
            dialog._tabs.setCurrentIndex(1)
            dialog.show()
            _wait_for_dialog(dialog, app)
            destination = output / f"outcome-{state}.png"
            if not dialog.grab().save(str(destination), "PNG"):
                raise RuntimeError(f"failed to render {destination.name}")
            dialog.close()
            app.processEvents()
    return 0


def _seed(store: SQLiteStore, *, settled: bool) -> None:
    entry_date = date(2026, 8, 10)
    target_date = date(2026, 8, 11)
    created_at = datetime(2026, 8, 10, 14, 45, tzinfo=SHANGHAI)
    entries: list[dict[str, object]] = []
    count = 6 if settled else 3
    for index in range(count):
        slot = "09:45" if index < 3 else "14:45"
        entry_ts = datetime.fromisoformat(f"{entry_date.isoformat()}T{slot}:00+08:00")
        entries.append(
            {
                "entry_snapshot_id": index + 1,
                "entry_alert_id": index + 1,
                "entry_trade_date": entry_date.isoformat(),
                "slot": slot,
                "rank": index % 3 + 1,
                "code": f"{index + 1:06d}.SZ",
                "name": f"复盘示例{index + 1}",
                "entry_price": 10.0 + index,
                "entry_source_ts": entry_ts.isoformat(),
                "target_trade_date": target_date.isoformat(),
                "target_slot": slot,
                "quality": "GOOD",
                "provider_version": "deterministic-screenshot-v1",
                "config_version": "candidate-outcomes-v1",
                "app_version": "0.6.0a1",
                "created_at": created_at.isoformat(),
                "updated_at": created_at.isoformat(),
                "safe_reason": None,
            }
        )
    store.create_candidate_outcomes(entries)
    if not settled:
        return
    exits = (10.5, 10.7, 12.0, 13.6, 13.7, 15.0)
    rows = store.list_candidate_outcomes(trading_days=None)
    for row, exit_price in zip(rows, exits):
        entry_price = float(row["entry_price"])
        value = return_pct(entry_price, exit_price)
        slot = str(row["slot"])
        exit_ts = datetime.fromisoformat(f"{target_date.isoformat()}T{slot}:00+08:00")
        store.settle_candidate_outcome(
            int(row["id"]),
            exit_price=exit_price,
            exit_source_ts=exit_ts.isoformat(),
            return_pct=value,
            outcome=classify_return(value).value,
            settlement_method="historical_minute",
            quality="GOOD",
            updated_at=(created_at + timedelta(days=1)).isoformat(),
        )


def _wait_for_dialog(dialog: HistoryDialog, app: QApplication) -> None:
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        app.processEvents()
        alert_done = not dialog._worker.isRunning()
        outcome_worker = dialog._outcomes._worker
        outcome_done = outcome_worker is None or not outcome_worker.isRunning()
        if alert_done and outcome_done:
            settled_until = time.monotonic() + 0.25
            while time.monotonic() < settled_until:
                app.processEvents()
                time.sleep(0.01)
            return
        time.sleep(0.01)
    raise TimeoutError("outcome screenshot workers did not finish")


if __name__ == "__main__":
    raise SystemExit(main())
