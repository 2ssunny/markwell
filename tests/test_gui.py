"""markwell/gui/*: MainWindow, ConvertTab/SyncCheckTab, and workers.py's
QThread lifecycle.

markwell.gui.workers.process_files / .check_sync are monkeypatched with fakes
that sleep briefly and emit progress -- no real conversion, no I/O, no
dependency on Tesseract/Drive/Notion. Every test that constructs a MainWindow
also goes through the autouse `_isolate_user_data` fixture from conftest.py,
so it always sees an empty, throwaway user-data directory.
"""

import gc
import time

from markwell.checker_service import CheckSummary
from markwell.config import AppConfig
from markwell.converter_service import FileOutcome, ProcessSummary
from markwell.gui import workers as workers_mod
from markwell.gui.convert_tab import ConvertTab

from .conftest import pump_events_until, significant_qt_messages, spin_events


# ---------------------------------------------------------------------------
# Fake service functions (stand in for the real, I/O-heavy pipeline calls)
# ---------------------------------------------------------------------------


def make_fake_process_files(delay=0.02, steps=3, ignore_cancel=False, record=None):
    def fake_process_files(cfg, files=None, log=lambda m: None, progress=lambda a, b: None, should_cancel=None, archive_originals=None):
        if record is not None:
            record["cfg"] = cfg
        cancelled = should_cancel or (lambda: False)
        log("[fake] starting")
        for i in range(steps):
            if not ignore_cancel and cancelled():
                return ProcessSummary(total=steps, converted=i, cancelled=True)
            time.sleep(delay)
            progress(i, steps)
        progress(steps, steps)
        return ProcessSummary(
            total=steps, converted=steps, outcomes=[FileOutcome("fake.pdf", "converted", "pdf-inspector")]
        )

    return fake_process_files


def make_fake_check_sync(delay=0.02, steps=3):
    def fake_check_sync(cfg, log=lambda m: None, progress=lambda a, b: None, should_cancel=None):
        cancelled = should_cancel or (lambda: False)
        for i in range(steps):
            if cancelled():
                return CheckSummary(total=steps, intact=i, cancelled=True)
            time.sleep(delay)
            progress(i, steps)
        progress(steps, steps)
        return CheckSummary(total=steps, intact=steps)

    return fake_check_sync


# ---------------------------------------------------------------------------
# Construction / basic Qt hygiene
# ---------------------------------------------------------------------------


def test_main_window_constructs_and_shows_with_no_qt_warnings(qapp):
    from PySide6.QtCore import qInstallMessageHandler

    messages = []
    qInstallMessageHandler(lambda mode, ctx, msg: messages.append(msg))
    try:
        from markwell.gui.main_window import MainWindow

        window = MainWindow()
        window.show()
        spin_events(0.1)
        window.close()
        spin_events(0.1)
        del window
        gc.collect()
        spin_events(0.1)
    finally:
        qInstallMessageHandler(None)

    assert significant_qt_messages(messages) == []


def test_no_qthread_destroyed_warning_across_a_full_job_and_window_close(qapp, monkeypatch):
    from PySide6.QtCore import qInstallMessageHandler

    from markwell.gui.main_window import MainWindow

    monkeypatch.setattr(workers_mod, "process_files", make_fake_process_files(delay=0.02, steps=3))

    messages = []
    qInstallMessageHandler(lambda mode, ctx, msg: messages.append(msg))
    try:
        window = MainWindow()
        window.show()
        window.convert_tab._start_conversion()
        pump_events_until(lambda: not window.is_job_running, timeout=5.0)
        spin_events(0.1)  # let QThread.finished / deleteLater actually settle
        window.close()
        spin_events(0.1)
        del window
        gc.collect()
        spin_events(0.1)
    finally:
        qInstallMessageHandler(None)

    assert not any("Destroyed while thread is still running" in m for m in messages)
    assert significant_qt_messages(messages) == []


# ---------------------------------------------------------------------------
# Cross-thread signal delivery
# ---------------------------------------------------------------------------


def test_service_runs_off_gui_thread_signals_arrive_on_gui_thread(qapp, monkeypatch):
    from PySide6.QtCore import QObject, QThread

    service_thread = {}

    def fake_process_files(cfg, files=None, log=lambda m: None, progress=lambda a, b: None, should_cancel=None, archive_originals=None):
        service_thread["thread"] = QThread.currentThread()
        log("hi")
        progress(1, 1)
        return ProcessSummary(total=1, converted=1)

    monkeypatch.setattr(workers_mod, "process_files", fake_process_files)

    gui_thread = QThread.currentThread()
    seen = {}

    class Receiver(QObject):
        def on_log(self, msg):
            seen["log"] = QThread.currentThread()

        def on_progress(self, done, total):
            seen["progress"] = QThread.currentThread()

        def on_finished(self, summary):
            seen["finished"] = QThread.currentThread()

    receiver = Receiver()  # a real QObject bound method as the receiver, not a plain function
    worker = workers_mod.ConvertWorker(AppConfig())
    worker.log.connect(receiver.on_log)
    worker.progress.connect(receiver.on_progress)
    worker.finished.connect(receiver.on_finished)

    thread = workers_mod.start_worker(worker)
    pump_events_until(lambda: "finished" in seen, timeout=5.0)
    pump_events_until(lambda: thread.isFinished(), timeout=5.0)

    assert service_thread["thread"] is not None
    assert service_thread["thread"] != gui_thread, "process_files must run off the GUI thread"
    assert seen["log"] == gui_thread
    assert seen["progress"] == gui_thread
    assert seen["finished"] == gui_thread


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------


def test_cancel_stops_the_run_early(qapp, monkeypatch):
    monkeypatch.setattr(workers_mod, "process_files", make_fake_process_files(delay=0.05, steps=50))

    result = {}
    worker = workers_mod.ConvertWorker(AppConfig())
    worker.finished.connect(lambda summary: result.update(summary=summary))
    thread = workers_mod.start_worker(worker)

    spin_events(0.12)  # let a few iterations run first
    worker.cancel()

    ok = pump_events_until(lambda: "summary" in result, timeout=5.0)
    pump_events_until(lambda: thread.isFinished(), timeout=5.0)

    assert ok
    assert result["summary"].cancelled is True
    assert result["summary"].converted < 50, "cancellation must stop the run well before completion"


# ---------------------------------------------------------------------------
# Worker/thread reference lifecycle
# ---------------------------------------------------------------------------


def test_worker_refs_are_cleared_only_after_qthread_finished_not_on_terminal_signal(main_window, monkeypatch):
    call_log = []
    orig_on_finished = ConvertTab._on_finished
    orig_on_thread_finished = ConvertTab._on_thread_finished

    def spy_on_finished(self, summary):
        call_log.append(("on_finished", self._worker is not None, self._thread is not None))
        orig_on_finished(self, summary)

    def spy_on_thread_finished(self):
        call_log.append(("on_thread_finished:entry", self._worker is not None, self._thread is not None))
        orig_on_thread_finished(self)
        call_log.append(("on_thread_finished:exit", self._worker is not None, self._thread is not None))

    monkeypatch.setattr(ConvertTab, "_on_finished", spy_on_finished)
    monkeypatch.setattr(ConvertTab, "_on_thread_finished", spy_on_thread_finished)
    monkeypatch.setattr(workers_mod, "process_files", make_fake_process_files(delay=0.02, steps=2))

    main_window.convert_tab._start_conversion()
    pump_events_until(lambda: len(call_log) >= 3, timeout=5.0)

    assert call_log[0] == ("on_finished", True, True), "refs must still be set while handling the worker's own finished signal"
    assert call_log[1] == ("on_thread_finished:entry", True, True)
    assert call_log[2] == ("on_thread_finished:exit", False, False), "refs are cleared only once QThread.finished fires"


# ---------------------------------------------------------------------------
# Mutual exclusion between the two tabs' jobs
# ---------------------------------------------------------------------------


def test_convert_and_sync_check_are_mutually_exclusive(main_window, monkeypatch):
    main_window.cfg.enable_drive = True  # so sync-check's start button is enabled at baseline
    monkeypatch.setattr(workers_mod, "process_files", make_fake_process_files(delay=0.05, steps=3))
    monkeypatch.setattr(workers_mod, "check_sync", make_fake_check_sync(delay=0.05, steps=3))

    main_window.sync_check_tab._refresh_availability()
    assert main_window.sync_check_tab.start_btn.isEnabled()

    main_window.convert_tab._start_conversion()

    assert not main_window.convert_tab.start_btn.isEnabled()
    assert not main_window.sync_check_tab.start_btn.isEnabled(), "starting convert must disable sync-check's start button"

    pump_events_until(lambda: not main_window.is_job_running, timeout=5.0)
    assert main_window.sync_check_tab.start_btn.isEnabled()
    assert main_window.convert_tab.start_btn.isEnabled()


def test_starting_sync_check_disables_convert_start_button(main_window, monkeypatch):
    main_window.cfg.enable_drive = True
    monkeypatch.setattr(workers_mod, "check_sync", make_fake_check_sync(delay=0.05, steps=3))

    main_window.sync_check_tab._start_check()

    assert not main_window.sync_check_tab.start_btn.isEnabled()
    assert not main_window.convert_tab.start_btn.isEnabled(), "starting sync-check must disable convert's start button"

    pump_events_until(lambda: not main_window.is_job_running, timeout=5.0)
    assert main_window.convert_tab.start_btn.isEnabled()


# ---------------------------------------------------------------------------
# Config snapshot
# ---------------------------------------------------------------------------


def test_worker_holds_a_config_snapshot_immune_to_later_mutation(main_window, monkeypatch):
    captured = {}

    def fake_process_files(cfg, files=None, log=lambda m: None, progress=lambda a, b: None, should_cancel=None, archive_originals=None):
        time.sleep(0.1)
        captured["ocr_lang"] = cfg.ocr_lang
        return ProcessSummary(total=0)

    monkeypatch.setattr(workers_mod, "process_files", fake_process_files)

    main_window.cfg.ocr_lang = "eng"
    main_window.convert_tab._start_conversion()
    main_window.cfg.ocr_lang = "kor"  # mutated immediately after the worker snapshot is taken

    ok = pump_events_until(lambda: "ocr_lang" in captured, timeout=5.0)

    assert ok
    assert captured["ocr_lang"] == "eng", "the run must see the config as it was when it started, not later edits"
    assert main_window.cfg.ocr_lang == "kor"


# ---------------------------------------------------------------------------
# Window close behaviour
# ---------------------------------------------------------------------------


def test_close_is_refused_while_job_ignores_cancellation_then_accepted_once_done(main_window, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    warn_calls = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warn_calls.append(a))
    monkeypatch.setattr(
        workers_mod, "process_files", make_fake_process_files(delay=1.2, steps=3, ignore_cancel=True)
    )

    main_window.convert_tab._start_conversion()
    spin_events(0.2)  # let the job actually start running

    closed = main_window.close()

    assert closed is False, "closing must be refused while a job that ignores cancel() is still running"
    assert warn_calls, "QMessageBox.warning must be shown explaining why the window didn't close"

    ok = pump_events_until(lambda: not main_window.is_job_running, timeout=10.0)
    assert ok, "fake job should have finished well within the timeout"

    closed_again = main_window.close()
    assert closed_again is True, "closing must be accepted once the job has finished"
