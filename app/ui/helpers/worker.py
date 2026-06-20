"""Run blocking callables without touching Qt widgets from worker threads."""

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Qt, Signal, Slot
from PySide6.QtWidgets import QApplication

from app.db.engine import SessionLocal


class _WorkerSignals(QObject):
    result = Signal(object)
    error = Signal(object)
    finished = Signal()


class _Worker(QRunnable):
    def __init__(self, function: Callable[[], Any]):
        super().__init__()
        self.function = function
        self.signals = _WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            result = self.function()
        except Exception as error:
            self.signals.error.emit(error)
        else:
            self.signals.result.emit(result)
        finally:
            SessionLocal.remove()
            self.signals.finished.emit()


def run_in_background(
    owner: QObject,
    function: Callable[[], Any],
    *,
    on_result: Callable[[Any], None],
    on_error: Callable[[Exception], None],
    on_finished: Callable[[], None] | None = None,
) -> _Worker:
    """Start *function* and deliver all callbacks on the owner's Qt thread."""
    worker = _Worker(function)
    active = getattr(owner, "_background_workers", None)
    if active is None:
        active = set()
        owner._background_workers = active
    active.add(worker)

    app = QApplication.instance()
    if app is not None:
        app.setOverrideCursor(Qt.WaitCursor)

    worker.signals.result.connect(on_result)
    worker.signals.error.connect(on_error)

    def cleanup() -> None:
        active.discard(worker)
        current_app = QApplication.instance()
        if current_app is not None:
            current_app.restoreOverrideCursor()
        if on_finished is not None:
            on_finished()

    worker.signals.finished.connect(cleanup)
    QThreadPool.globalInstance().start(worker)
    return worker
