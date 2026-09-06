"""Keep read workers alive independently of a dismissed view."""
from collections.abc import Callable

from PySide6.QtCore import QThread, Signal

_running: set[QThread] = set()


def start_worker(worker: QThread) -> None:
    _running.add(worker)

    def finished() -> None:
        _running.discard(worker)
        worker.deleteLater()

    worker.finished.connect(finished)
    worker.start()


class TaskWorker(QThread):
    loaded = Signal(object, str)

    def __init__(self, task: Callable[[], object]) -> None:
        super().__init__()
        self._task = task

    def run(self) -> None:
        try:
            self.loaded.emit(self._task(), "")
        except Exception as error:  # noqa: BLE001 - UI presents a retryable failure
            self.loaded.emit(None, type(error).__name__)
