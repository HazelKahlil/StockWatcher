"""Background report work that never keeps Windows shutdown waiting."""
from collections.abc import Callable
from threading import Lock, Thread

from PySide6.QtCore import QObject, QTimer, Signal

_running: set['TaskWorker'] = set()


class TaskWorker(QObject):
    loaded = Signal(object, str)

    def __init__(self, task: Callable[[], object]) -> None:
        super().__init__()
        self._task = task
        self._lock = Lock()
        self._result: tuple[object, str] | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(25)
        self._timer.timeout.connect(self._poll)

    def start(self) -> None:
        Thread(target=self._run, name='stockwatcher-report', daemon=True).start()
        self._timer.start()

    def _run(self) -> None:
        try:
            result = (self._task(), '')
        except Exception as error:  # noqa: BLE001 - safe class name only
            result = (None, type(error).__name__)
        with self._lock:
            self._result = result

    def _poll(self) -> None:
        with self._lock:
            result = self._result
            self._result = None
        if result is None:
            return
        self._timer.stop()
        self.loaded.emit(*result)
        _running.discard(self)
        self.deleteLater()


def start_worker(worker: TaskWorker) -> None:
    _running.add(worker)
    worker.start()
