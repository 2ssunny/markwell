"""'동기화 점검' tab: reconcile local history against Google Drive."""

from typing import Optional

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import config as config_mod
from .workers import CheckWorker, start_worker


class SyncCheckTab(QWidget):
    def __init__(self, main_window):
        super().__init__(main_window)
        self.main_window = main_window
        self.cfg: config_mod.AppConfig = main_window.cfg

        self._thread = None
        self._worker: Optional[CheckWorker] = None
        self._running = False  # True while THIS tab's own job is in flight

        self._build_ui()
        self._refresh_availability()

        main_window.config_changed.connect(self._refresh_availability)
        main_window.job_started.connect(self._refresh_availability)
        main_window.job_finished.connect(self._refresh_availability)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        explanation = QLabel(
            "Google Drive에서 삭제된 변환 결과가 있는지 확인하고, 로컬 변환 기록과 연결된 "
            "Notion 페이지를 함께 정리합니다."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        self.disabled_reason_label = QLabel("Google Drive 연동이 꺼져 있어 점검할 수 없습니다.")
        self.disabled_reason_label.setStyleSheet("color: red;")
        layout.addWidget(self.disabled_reason_label)

        self.busy_label = QLabel("다른 작업(변환)이 진행 중입니다. 완료 후 다시 시도하세요.")
        self.busy_label.setStyleSheet("color: gray;")
        self.busy_label.setVisible(False)
        layout.addWidget(self.busy_label)

        run_row = QHBoxLayout()
        self.start_btn = QPushButton("점검 시작")
        self.start_btn.clicked.connect(self._start_check)
        self.cancel_btn = QPushButton("취소")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._cancel_check)
        run_row.addWidget(self.start_btn)
        run_row.addWidget(self.cancel_btn)
        run_row.addStretch(1)
        layout.addLayout(run_row)

        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

        self.result_list = QListWidget()
        layout.addWidget(self.result_list)

        self.summary_label = QLabel("")
        layout.addWidget(self.summary_label)

    def _refresh_availability(self) -> None:
        enabled = self.cfg.enable_drive
        other_running = self.main_window.is_job_running and not self._running
        self.start_btn.setEnabled(enabled and not self._running and not other_running)
        self.disabled_reason_label.setVisible(not enabled)
        self.busy_label.setVisible(enabled and other_running)

    def _start_check(self) -> None:
        if not self.cfg.enable_drive:
            return
        # Defensive: the start button should already be disabled while any
        # job is running, but don't rely solely on that.
        if not self.main_window.try_start_job():
            return

        self.result_list.clear()
        self.progress_bar.setValue(0)
        self.progress_bar.setMaximum(0)
        self.summary_label.setText("")

        worker = CheckWorker(self.cfg)
        worker.log.connect(self.main_window.append_log)
        worker.progress.connect(self._on_progress)
        worker.finished.connect(self._on_finished)
        worker.failed.connect(self._on_failed)
        self._worker = worker
        self._thread = start_worker(worker, on_thread_finished=self._on_thread_finished)

        self._set_running(True)

    def _cancel_check(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self.main_window.append_log("[취소 요청] 진행 중인 항목까지 확인하고 중단합니다...")
        self.cancel_btn.setEnabled(False)

    def _on_progress(self, done: int, total: int) -> None:
        self.progress_bar.setMaximum(max(total, 1))
        self.progress_bar.setValue(done)

    def _on_finished(self, summary) -> None:
        for name in summary.removed_names:
            self.result_list.addItem(f"[삭제됨] {name}")

        self.progress_bar.setMaximum(max(summary.total, 1))
        self.progress_bar.setValue(summary.total)

        text = (
            f"총 {summary.total} / 삭제됨 {summary.removed} / "
            f"정상 {summary.intact} / 미확인 {summary.unchecked}"
        )
        if summary.cancelled:
            text += " (취소됨)"
        self.summary_label.setText(text)

        self._set_running(False)
        self.main_window.notify_job_finished()

    def _on_failed(self, message: str) -> None:
        self.main_window.append_log(f"[오류] 동기화 점검 작업이 예기치 않게 종료되었습니다:\n{message}")
        QMessageBox.critical(self, "점검 실패", "동기화 점검 중 오류가 발생했습니다. 로그를 확인하세요.")
        self._set_running(False)
        self.main_window.notify_job_finished()

    def _set_running(self, running: bool) -> None:
        self._running = running
        self.cancel_btn.setEnabled(running)
        self._refresh_availability()

    def _on_thread_finished(self) -> None:
        """Runs once QThread.finished actually fires -- i.e. once the thread
        has truly stopped, not merely when the worker signaled done/failed.
        Dropping the last reference any earlier can destroy a live QThread.
        See workers.start_worker's docstring."""
        self._thread = None
        self._worker = None

    # -- lifecycle ---------------------------------------------------------------

    def shutdown(self) -> bool:
        """Best-effort stop of any in-flight worker so app close doesn't crash.

        Returns True once no job is running (safe to close), False if the
        worker is still finishing after the grace period -- never calls
        ``terminate()``, since killing a thread mid-upload is exactly what
        corrupts history.json. The caller must not destroy this tab (or the
        window) when this returns False.
        """
        if self._worker is None and self._thread is None:
            return True
        if self._worker is not None:
            self._worker.cancel()
        if self._thread is not None:
            self._thread.quit()
            if not self._thread.wait(3000):
                return False
        return True
