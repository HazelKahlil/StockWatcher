from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_if_present(relative: str, old: str, new: str) -> None:
    path = ROOT / relative
    content = path.read_text(encoding="utf-8")
    if new in content:
        return
    count = content.count(old)
    if count != 1:
        raise RuntimeError(f"{relative}: expected one match, found {count}: {old!r}")
    path.write_text(content.replace(old, new, 1), encoding="utf-8")


replace_if_present(
    "src/stock_watcher/ui/data_source_settings.py",
    "from PySide6.QtGui import QCloseEvent\n",
    "",
)
replace_if_present(
    "src/stock_watcher/ui/data_source_settings.py",
    "    QFormLayout,\n",
    "    QFormLayout,\n    QFrame,\n",
)
replace_if_present(
    "src/stock_watcher/ui/data_source_settings.py",
    "        self.setMinimumSize(620, 460)\n",
    "        self.setMinimumSize(680, 460)\n",
)
replace_if_present(
    "src/stock_watcher/ui/tushare_v1_session.py",
    "from time import sleep as sleep_seconds\n",
    "",
)
