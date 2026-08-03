from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from stock_watcher.storage import SQLiteStore

AUDIT_FIELDS = (
    "scan_id",
    "completed_at",
    "trigger_type",
    "warmup_state",
    "raw_rank",
    "code",
    "name",
    "sector",
    "sector_type",
    "total_score",
    "level",
    "is_formal",
    "velocity_available",
    "selected_raw",
    "selected_stable",
    "decision",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export raw-vs-stable Top3 evidence from StockWatcher SQLite."
    )
    parser.add_argument("database", type=Path)
    parser.add_argument("trade_date", help="YYYY-MM-DD")
    parser.add_argument("output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    store = SQLiteStore(args.database, read_only=True)
    rows = store.list_scan_runs(args.trade_date)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "scan-runs.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    with (args.output / "candidate-audit.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=AUDIT_FIELDS,
        )
        writer.writeheader()
        for scan in rows:
            try:
                audit = json.loads(str(scan.get("audit_json") or "{}"))
            except json.JSONDecodeError:
                continue
            for candidate in audit.get("rows", []):
                if not isinstance(candidate, dict):
                    continue
                writer.writerow(
                    {
                        "scan_id": scan["id"],
                        "completed_at": scan["completed_at"],
                        "trigger_type": scan["trigger_type"],
                        "warmup_state": audit.get("warmup_state", "unknown"),
                        **{
                            key: candidate.get(key)
                            for key in AUDIT_FIELDS
                            if key
                            not in {
                                "scan_id",
                                "completed_at",
                                "trigger_type",
                                "warmup_state",
                            }
                        },
                    }
                )
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
