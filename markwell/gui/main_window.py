"""Main application window: tabs, shared log dock, and status bar."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDockWidget,
    QFileDialog,
    QHBoxLayout,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import config as config_mod
from .. import paths
from .convert_tab import ConvertTab
from .settings_tab import SettingsTab
from .sync_check_tab import SyncCheckTab


class MainWindow(QMainWindow):
    """Top-level window: three tabs sharing one config instance and one log."""

    # Emitted whenever the shared AppConfig instance changes (settings saved,
    # an integration toggle flipped). Tabs re-read cfg fields on this signal.
    config_changed = Signal()

    # Emitted when a tab claims/releases the single shared job slot (see
    # try_start_job/notify_job_finished below). The 변환 and 동기화 점검 tabs
    # both mutate history.json, so only one job may run at a time; other tabs
    # (and the 설정 tab's 저장 button) react to these to disable themselves.
    job_started = Signal()
    job_finished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("MarkItDown Batch")
        self.resize(1000, 700)
        self.setMinimumSize(800, 600)

        self.cfg = config_mod.load_config()
        self.is_job_running = False

        self._build_log_dock()

        self.convert_tab = ConvertTab(self)
        self.sync_check_tab = SyncCheckTab(self)
        self.settings_tab = SettingsTab(self)

        tabs = QTabWidget(self)
        tabs.addTab(self.convert_tab, "변환")
        tabs.addTab(self.sync_check_tab, "동기화 점검")
        tabs.addTab(self.settings_tab, "설정")
        self.setCentralWidget(tabs)

        self.statusBar().showMessage(f"데이터 폴더: {paths.user_data_dir()}")

    # -- shared log ----------------------------------------------------------

    def _build_log_dock(self) -> None:
        dock = QDockWidget("로그", self)
        dock.setFeatures(QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)
        dock.setAllowedAreas(Qt.DockWidgetArea.BottomDockWidgetArea)

        container = QWidget()
        layout = QVBoxLayout(container)

        toolbar = QHBoxLayout()
        toolbar.addStretch(1)
        clear_btn = QPushButton("로그 지우기")
        clear_btn.clicked.connect(self._clear_log)
        save_btn = QPushButton("로그 저장")
        save_btn.clicked.connect(self._save_log)
        toolbar.addWidget(clear_btn)
        toolbar.addWidget(save_btn)
        layout.addLayout(toolbar)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(20000)
        layout.addWidget(self.log_view)

        dock.setWidget(container)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, dock)

    def append_log(self, text: str) -> None:
        self.log_view.appendPlainText(text)
        bar = self.log_view.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _clear_log(self) -> None:
        self.log_view.clear()

    def _save_log(self) -> None:
        file_path, _ = QFileDialog.getSaveFileName(self, "로그 저장", "log.txt", "텍스트 파일 (*.txt)")
        if not file_path:
            return
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(self.log_view.toPlainText())
        except OSError as exc:
            QMessageBox.warning(self, "로그 저장 실패", f"로그를 저장하지 못했습니다: {exc}")

    # -- shared config ---------------------------------------------------------

    def notify_config_changed(self) -> None:
        """Call after mutating/saving ``self.cfg`` so other tabs re-read it."""
        self.config_changed.emit()

    # -- shared job slot ---------------------------------------------------------
    #
    # 변환 and 동기화 점검 both write to history.json; letting them run at the
    # same time is confusing even though history.py is (separately) being made
    # thread-safe. This is a GUI-side mutual-exclusion guard only -- no file
    # locking here, that is handled elsewhere.

    def try_start_job(self) -> bool:
        """Claim the single shared job slot. Returns False if a job is already
        running -- the caller's start button should already be disabled in
        that case, so this is a defensive fallback, not the primary gate."""
        if self.is_job_running:
            return False
        self.is_job_running = True
        self.job_started.emit()
        return True

    def notify_job_finished(self) -> None:
        """Release the shared job slot. Call once a worker's ``finished``/
        ``failed`` signal has been handled (business logic done) -- no need
        to wait for the QThread itself to fully wind down first."""
        self.is_job_running = False
        self.job_finished.emit()

    # -- lifecycle -------------------------------------------------------------

    def closeEvent(self, event) -> None:
        convert_stopped = self.convert_tab.shutdown()
        sync_stopped = self.sync_check_tab.shutdown()
        if not (convert_stopped and sync_stopped):
            QMessageBox.warning(
                self,
                "종료 불가",
                "진행 중인 작업이 아직 끝나지 않아 창을 닫을 수 없습니다.\n"
                "작업이 끝난 후 다시 종료해주세요.",
            )
            event.ignore()
            return
        super().closeEvent(event)
