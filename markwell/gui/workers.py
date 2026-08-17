"""QObject-based background workers for the GUI's long-running service calls.

``process_files``/``check_sync`` are blocking and perform network I/O (Google
Drive OAuth, Notion API), so they must never run on the GUI thread. Each
worker is moved onto a QThread via ``moveToThread`` -- QThread itself is
never subclassed, per Qt's recommended worker-object pattern.
"""

import copy
import threading
import traceback
from pathlib import Path
from typing import Optional, Sequence

from PySide6.QtCore import QObject, QThread, Signal

from .. import config as config_mod
from ..checker_service import check_sync
from ..converter_service import process_files


class ConvertWorker(QObject):
    """Runs ``converter_service.process_files`` off the GUI thread."""

    log = Signal(str)
    progress = Signal(int, int)  # done, total
    finished = Signal(object)  # ProcessSummary
    failed = Signal(str)

    def __init__(self, cfg: config_mod.AppConfig, files: Optional[Sequence[Path]] = None):
        super().__init__()
        # Snapshot the config at start time: the GUI's AppConfig instance is
        # mutable and shared with the 설정 tab, which can edit/save it while
        # this worker is mid-run. Deep-copy so the run sees a consistent view
        # even if the user changes settings before it finishes.
        self._cfg = copy.deepcopy(cfg)
        self._files = list(files) if files is not None else None
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    def run(self) -> None:
        try:
            summary = process_files(
                self._cfg,
                files=self._files,
                log=self.log.emit,
                progress=self.progress.emit,
                should_cancel=self._cancel_event.is_set,
            )
        except Exception:
            self.failed.emit(traceback.format_exc())
            return
        self.finished.emit(summary)


class CheckWorker(QObject):
    """Runs ``checker_service.check_sync`` off the GUI thread."""

    log = Signal(str)
    progress = Signal(int, int)  # done, total
    finished = Signal(object)  # CheckSummary
    failed = Signal(str)

    def __init__(self, cfg: config_mod.AppConfig):
        super().__init__()
        # See ConvertWorker.__init__: snapshot so a concurrent 설정 save
        # can't change the config out from under an in-flight run.
        self._cfg = copy.deepcopy(cfg)
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    def run(self) -> None:
        try:
            summary = check_sync(
                self._cfg,
                log=self.log.emit,
                progress=self.progress.emit,
                should_cancel=self._cancel_event.is_set,
            )
        except Exception:
            self.failed.emit(traceback.format_exc())
            return
        self.finished.emit(summary)


def start_worker(worker: QObject, on_thread_finished=None) -> QThread:
    """Create a QThread for ``worker``, wire the standard lifecycle, and start it.

    Connect the worker's own ``log``/``progress``/``finished``/``failed``
    signals to your slots *before* calling this, since the thread starts
    (and may emit) immediately.

    Follows Qt's documented worker-object cleanup pattern: either terminal
    signal quits the thread and schedules the worker for deletion; the
    thread deletes itself once its event loop has actually finished. The
    caller must keep a reference to the returned QThread (and to ``worker``)
    for as long as the job runs -- a garbage-collected QThread crashes the
    app.

    IMPORTANT: the worker's ``finished``/``failed`` signals fire *before*
    the thread has actually stopped -- at that point ``thread.quit()`` has
    only just been queued, the event loop is still winding down. Callers
    must NOT drop their reference to the returned QThread from a slot
    connected to the worker's terminal signals; doing so can garbage-collect
    a QThread that is still running, which aborts the process ("QThread:
    Destroyed while thread is still running"). Pass ``on_thread_finished``
    (connected here, before ``thread.start()``, so it can never be missed)
    and clear references there instead -- that slot only runs once
    ``QThread.finished`` fires, i.e. once the thread has truly stopped.
    """
    thread = QThread()
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.finished.connect(thread.quit)
    worker.finished.connect(worker.deleteLater)
    worker.failed.connect(thread.quit)
    worker.failed.connect(worker.deleteLater)
    thread.finished.connect(thread.deleteLater)
    if on_thread_finished is not None:
        thread.finished.connect(on_thread_finished)
    thread.start()
    return thread
