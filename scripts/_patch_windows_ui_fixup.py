from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_if_present(relative: str, old: str, new: str) -> None:
    path = ROOT / relative
    content = path.read_text(encoding="utf-8")
    if new and new in content:
        return
    if old not in content:
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
    "from time import monotonic as monotonic_time\n",
    "from time import monotonic as monotonic_time\nfrom time import sleep as _sleep\n",
)
replace_if_present(
    "src/stock_watcher/ui/tushare_v1_session.py",
    "from .connection_state import ConnectionState as TqConnectionState\n\n\n@dataclass",
    '''from .connection_state import ConnectionState as TqConnectionState


def sleep_seconds(seconds: float) -> None:
    """Compatibility hook retained for deterministic tests and integrations."""

    _sleep(seconds)


@dataclass''',
)
replace_if_present(
    "src/stock_watcher/ui/tushare_v1_session.py",
    '''            except Exception as error:
                self._set_summary_retry(now)
                self._summary_issue = (
                    f"盘后回顾暂未生成（{type(error).__name__}），将在60秒后自动重试。"
                )
                return False
''',
    '''            except Exception:
                self._set_summary_retry(now)
                return False
''',
)
replace_if_present(
    "tests/test_automation_reliability.py",
    '''    assert task["state"] == AutomationTaskState.SUCCEEDED.value, (
        task,
        session._summary_issue,
    )
''',
    '''    assert task["state"] == AutomationTaskState.SUCCEEDED.value
''',
)
replace_if_present(
    "tests/test_windows_desktop_stability.py",
    '''def application() -> QApplication:
    existing = QApplication.instance()
    return existing if isinstance(existing, QApplication) else QApplication([])
''',
    '''def application() -> QApplication:
    existing = QApplication.instance()
    app = existing if isinstance(existing, QApplication) else QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app
''',
)
