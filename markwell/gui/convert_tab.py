"""'변환' tab: integration toggles, file queue, and the conversion run."""

from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import config as config_mod
from .. import dispatch
from .workers import ConvertWorker, start_worker

_STATUS_LABELS = {
    "converted": "완료",
    "duplicate": "중복",
    "skipped": "건너뜀",
    "failed": "실패",
    "cancelled": "취소",
}


class FileQueueList(QListWidget):
    """QListWidget accepting drag-and-drop of local files/folders."""

    paths_dropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:
        urls = event.mimeData().urls()
        local_paths = [Path(u.toLocalFile()) for u in urls if u.isLocalFile()]
        local_paths = [p for p in local_paths if p.exists()]
        if local_paths:
            self.paths_dropped.emit(local_paths)
            event.acceptProposedAction()
        else:
            event.ignore()


class ConvertTab(QWidget):
    def __init__(self, main_window):
        super().__init__(main_window)
        self.main_window = main_window
        self.cfg: config_mod.AppConfig = main_window.cfg

        self._queued_paths: List[Path] = []
        self._thread = None
        self._worker: Optional[ConvertWorker] = None
        self._running = False  # True while THIS tab's own job is in flight

        self._build_ui()
        self._refresh_integration_status()
        self._refresh_mode_label()
        self._sync_start_button()

        main_window.config_changed.connect(self._refresh_integration_status)
        main_window.job_started.connect(self._sync_start_button)
        main_window.job_finished.connect(self._sync_start_button)

    # -- UI construction -------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        layout.addWidget(self._build_integration_box())

        layout.addWidget(self._build_queue_box())

        run_row = QHBoxLayout()
        self.start_btn = QPushButton("변환 시작")
        self.start_btn.clicked.connect(self._start_conversion)
        self.cancel_btn = QPushButton("취소")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._cancel_conversion)
        run_row.addWidget(self.start_btn)
        run_row.addWidget(self.cancel_btn)
        run_row.addStretch(1)
        layout.addLayout(run_row)

        self.busy_label = QLabel("다른 작업(동기화 점검)이 진행 중입니다. 완료 후 다시 시도하세요.")
        self.busy_label.setStyleSheet("color: gray;")
        self.busy_label.setVisible(False)
        layout.addWidget(self.busy_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

        self.result_table = QTableWidget(0, 5)
        self.result_table.setHorizontalHeaderLabels(["이름", "상태", "엔진", "OCR 페이지", "메시지"])
        self.result_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.result_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.result_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self.result_table)

        self.summary_label = QLabel("")
        layout.addWidget(self.summary_label)

    def _build_integration_box(self) -> QWidget:
        box = QGroupBox("연동 설정")
        outer = QVBoxLayout(box)

        row = QHBoxLayout()
        self.drive_checkbox = QCheckBox("Google Drive 연동")
        self.drive_checkbox.setChecked(self.cfg.enable_drive)
        self.drive_checkbox.toggled.connect(self._on_drive_toggled)
        self.drive_status_label = QLabel("")
        row.addWidget(self.drive_checkbox)
        row.addWidget(self.drive_status_label)
        row.addSpacing(24)

        self.notion_checkbox = QCheckBox("Notion 연동")
        self.notion_checkbox.setChecked(self.cfg.enable_notion)
        self.notion_checkbox.toggled.connect(self._on_notion_toggled)
        self.notion_status_label = QLabel("")
        row.addWidget(self.notion_checkbox)
        row.addWidget(self.notion_status_label)
        row.addStretch(1)
        outer.addLayout(row)

        self.drive_hint_label = QLabel("Google Drive 연동이 꺼져 있어 변환 결과가 출력 폴더에 저장됩니다.")
        self.drive_hint_label.setStyleSheet("color: gray;")
        outer.addWidget(self.drive_hint_label)

        return box

    def _build_queue_box(self) -> QWidget:
        box = QGroupBox("파일 대기열")
        outer = QVBoxLayout(box)

        self.queue_list = FileQueueList()
        self.queue_list.paths_dropped.connect(self._add_paths)
        outer.addWidget(self.queue_list)

        btn_row = QHBoxLayout()
        add_files_btn = QPushButton("파일 추가")
        add_files_btn.clicked.connect(self._add_files_dialog)
        add_folder_btn = QPushButton("폴더 추가")
        add_folder_btn.clicked.connect(self._add_folder_dialog)
        remove_btn = QPushButton("선택 삭제")
        remove_btn.clicked.connect(self._remove_selected)
        clear_btn = QPushButton("전체 비우기")
        clear_btn.clicked.connect(self._clear_queue)
        btn_row.addWidget(add_files_btn)
        btn_row.addWidget(add_folder_btn)
        btn_row.addWidget(remove_btn)
        btn_row.addWidget(clear_btn)
        btn_row.addStretch(1)
        outer.addLayout(btn_row)

        self.mode_label = QLabel("")
        outer.addWidget(self.mode_label)

        return box

    # -- integration toggles -----------------------------------------------------

    def _on_drive_toggled(self, checked: bool) -> None:
        self.cfg.enable_drive = checked
        config_mod.save_config(self.cfg)
        self.main_window.notify_config_changed()
        self._refresh_integration_status()

    def _on_notion_toggled(self, checked: bool) -> None:
        self.cfg.enable_notion = checked
        config_mod.save_config(self.cfg)
        self.main_window.notify_config_changed()
        self._refresh_integration_status()

    def _refresh_integration_status(self) -> None:
        # Sync checkbox states in case another tab (settings) changed cfg.
        if self.drive_checkbox.isChecked() != self.cfg.enable_drive:
            self.drive_checkbox.setChecked(self.cfg.enable_drive)
        if self.notion_checkbox.isChecked() != self.cfg.enable_notion:
            self.notion_checkbox.setChecked(self.cfg.enable_notion)

        if config_mod.token_path().exists():
            drive_text = "연결됨"
        elif config_mod.credentials_path().exists():
            drive_text = "인증 필요"
        else:
            drive_text = "credentials.json 없음"
        self.drive_status_label.setText(drive_text)

        self.notion_status_label.setText("설정됨" if self.cfg.notion_configured else "설정 필요")

        self.drive_hint_label.setVisible(not self.cfg.enable_drive)

    # -- queue management -----------------------------------------------------

    def _add_files_dialog(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "파일 추가")
        if files:
            self._add_paths([Path(f) for f in files])

    def _add_folder_dialog(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "폴더 추가")
        if folder:
            self._add_paths([Path(folder)])

    def _add_paths(self, paths_list: List[Path]) -> None:
        added = 0
        for p in paths_list:
            p = Path(p)
            if p.is_dir():
                for child in sorted(p.iterdir()):
                    if child.is_file() and self._add_single_file(child):
                        added += 1
            elif p.is_file():
                if self._add_single_file(p):
                    added += 1
        if added:
            self._refresh_mode_label()

    def _add_single_file(self, path: Path) -> bool:
        resolved = path.resolve()
        if any(resolved == q.resolve() for q in self._queued_paths):
            return False
        self._queued_paths.append(path)
        supported = dispatch.is_supported(path)
        text = path.name if supported else f"[미지원] {path.name}"
        item = QListWidgetItem(text)
        item.setData(Qt.ItemDataRole.UserRole, str(path))
        if not supported:
            item.setForeground(QColor("gray"))
        self.queue_list.addItem(item)
        return True

    def _remove_selected(self) -> None:
        for item in self.queue_list.selectedItems():
            path_str = item.data(Qt.ItemDataRole.UserRole)
            self._queued_paths = [p for p in self._queued_paths if str(p) != path_str]
            self.queue_list.takeItem(self.queue_list.row(item))
        self._refresh_mode_label()

    def _clear_queue(self) -> None:
        self.queue_list.clear()
        self._queued_paths = []
        self._refresh_mode_label()

    def _refresh_mode_label(self) -> None:
        # Whether originals get archived depends on how they got here, so say
        # it plainly -- a file picked from the user's own folder must not
        # quietly disappear out of it.
        if self._queued_paths:
            self.mode_label.setText(
                f"현재 대기열: {len(self._queued_paths)}개 파일을 변환합니다. "
                "직접 선택한 파일이므로 원본은 원래 위치에 그대로 둡니다."
            )
        else:
            input_dir = self.cfg.resolved_input_dir()
            processed_dir = self.cfg.resolved_processed_dir()
            self.mode_label.setText(
                f"대기열이 비어 있어 'input_files' 폴더를 사용합니다: {input_dir}\n"
                f"변환이 끝난 원본은 '{processed_dir.name}' 폴더로 이동합니다."
            )

    # -- conversion run --------------------------------------------------------

    def _start_conversion(self) -> None:
        # Defensive: the start button should already be disabled while any
        # job is running, but don't rely solely on that.
        if not self.main_window.try_start_job():
            return

        files = list(self._queued_paths) if self._queued_paths else None

        self.result_table.setRowCount(0)
        self.progress_bar.setValue(0)
        self.progress_bar.setMaximum(0)  # indeterminate until first progress signal
        self.summary_label.setText("")

        worker = ConvertWorker(self.cfg, files=files)
        worker.log.connect(self.main_window.append_log)
        worker.progress.connect(self._on_progress)
        worker.finished.connect(self._on_finished)
        worker.failed.connect(self._on_failed)
        self._worker = worker
        self._thread = start_worker(worker, on_thread_finished=self._on_thread_finished)

        self._set_running(True)

    def _cancel_conversion(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self.main_window.append_log("[취소 요청] 진행 중인 파일까지 마치고 중단합니다...")
        self.cancel_btn.setEnabled(False)

    def _on_progress(self, done: int, total: int) -> None:
        self.progress_bar.setMaximum(max(total, 1))
        self.progress_bar.setValue(done)

    def _on_finished(self, summary) -> None:
        for outcome in summary.outcomes:
            row = self.result_table.rowCount()
            self.result_table.insertRow(row)
            status_text = _STATUS_LABELS.get(outcome.status, outcome.status)
            values = [outcome.name, status_text, outcome.engine, str(outcome.ocr_pages or ""), outcome.message]
            for col, value in enumerate(values):
                self.result_table.setItem(row, col, QTableWidgetItem(value))

        self.progress_bar.setMaximum(max(summary.total, 1))
        self.progress_bar.setValue(summary.total)

        text = (
            f"변환 {summary.converted} / 중복 {summary.duplicates} / "
            f"건너뜀 {summary.skipped} / 실패 {summary.failed}"
        )
        if summary.cancelled:
            text += " (취소됨)"
        self.summary_label.setText(text)

        self._set_running(False)
        self.main_window.notify_job_finished()

    def _on_failed(self, message: str) -> None:
        self.main_window.append_log(f"[오류] 변환 작업이 예기치 않게 종료되었습니다:\n{message}")
        QMessageBox.critical(self, "변환 실패", "변환 작업 중 오류가 발생했습니다. 로그를 확인하세요.")
        self._set_running(False)
        self.main_window.notify_job_finished()

    def _set_running(self, running: bool) -> None:
        self._running = running
        self.cancel_btn.setEnabled(running)
        self.drive_checkbox.setEnabled(not running)
        self.notion_checkbox.setEnabled(not running)
        self._sync_start_button()

    def _sync_start_button(self) -> None:
        """Enable 변환 시작 unless this tab's own job is running, or the
        other tab's job is (they share history.json -- see MainWindow)."""
        other_running = self.main_window.is_job_running and not self._running
        self.start_btn.setEnabled(not self._running and not other_running)
        self.busy_label.setVisible(other_running)

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
