"""'설정' tab: NOTION/Drive/OCR/path settings, credential import, legacy migration."""

import shutil
from dataclasses import fields
from pathlib import Path

from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .. import config as config_mod
from .. import paths
from ..ocr.factory import get_ocr_engine

_LEGACY_FILES = ["config.json", "credentials.json", "token.json", "history.json"]


class SettingsTab(QWidget):
    def __init__(self, main_window):
        super().__init__(main_window)
        self.main_window = main_window
        self.cfg: config_mod.AppConfig = main_window.cfg

        self._build_ui()
        self._load_from_cfg()
        self._refresh_credentials_status()
        self._refresh_tesseract_status()
        self._refresh_job_running_state()

        main_window.job_started.connect(self._refresh_job_running_state)
        main_window.job_finished.connect(self._refresh_job_running_state)

    # -- UI construction ---------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)

        content = QWidget()
        scroll.setWidget(content)
        layout = QVBoxLayout(content)

        layout.addWidget(self._build_notion_box())
        layout.addWidget(self._build_drive_box())
        layout.addWidget(self._build_ocr_box())
        layout.addWidget(self._build_paths_box())
        layout.addWidget(self._build_tesseract_box())
        layout.addStretch(1)

        save_row = QHBoxLayout()
        self.job_running_label = QLabel("변환 또는 동기화 점검이 진행 중에는 저장할 수 없습니다.")
        self.job_running_label.setStyleSheet("color: gray;")
        self.job_running_label.setVisible(False)
        save_row.addWidget(self.job_running_label)
        save_row.addStretch(1)
        self.save_btn = QPushButton("저장")
        self.save_btn.clicked.connect(self._save)
        save_row.addWidget(self.save_btn)
        outer.addLayout(save_row)

    def _build_notion_box(self) -> QWidget:
        box = QGroupBox("Notion")
        form = QFormLayout(box)

        key_row = QHBoxLayout()
        self.notion_api_key_edit = QLineEdit()
        self.notion_api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.notion_api_key_toggle = QPushButton("표시")
        self.notion_api_key_toggle.setCheckable(True)
        self.notion_api_key_toggle.toggled.connect(self._toggle_api_key_visibility)
        key_row.addWidget(self.notion_api_key_edit)
        key_row.addWidget(self.notion_api_key_toggle)
        form.addRow("NOTION_API_KEY", key_row)

        self.notion_database_id_edit = QLineEdit()
        form.addRow("NOTION_DATABASE_ID", self.notion_database_id_edit)

        return box

    def _build_drive_box(self) -> QWidget:
        box = QGroupBox("Google Drive")
        form = QFormLayout(box)

        self.drive_folder_name_edit = QLineEdit()
        form.addRow("DRIVE_FOLDER_NAME", self.drive_folder_name_edit)

        cred_row = QHBoxLayout()
        import_cred_btn = QPushButton("credentials.json 가져오기")
        import_cred_btn.clicked.connect(self._import_credentials)
        self.credentials_status_label = QLabel("")
        cred_row.addWidget(import_cred_btn)
        cred_row.addWidget(self.credentials_status_label)
        cred_row.addStretch(1)
        form.addRow("자격 증명", cred_row)

        # Only meaningful in a frozen build: from source, user_data_dir() IS
        # the legacy app/ folder already, so "importing" it would copy files
        # onto themselves. Skip the control entirely in that case.
        if paths.is_frozen():
            legacy_row = QHBoxLayout()
            legacy_btn = QPushButton("기존 app 폴더에서 가져오기")
            legacy_btn.clicked.connect(self._import_legacy_folder)
            legacy_row.addWidget(legacy_btn)
            legacy_row.addStretch(1)
            form.addRow("마이그레이션", legacy_row)

        return box

    def _build_ocr_box(self) -> QWidget:
        box = QGroupBox("OCR")
        form = QFormLayout(box)

        self.ocr_lang_edit = QLineEdit()
        form.addRow("OCR_LANG", self.ocr_lang_edit)
        hint = QLabel("Tesseract 언어 코드를 '+'로 연결합니다 (예: eng+kor).")
        hint.setStyleSheet("color: gray;")
        form.addRow("", hint)

        return box

    def _build_paths_box(self) -> QWidget:
        box = QGroupBox("폴더")
        form = QFormLayout(box)

        default_input = str(paths.default_workspace_dir() / "input_files")
        default_processed = str(paths.default_workspace_dir() / "processed_files")
        default_output = str(paths.default_workspace_dir() / "output_files")

        self.input_dir_edit = QLineEdit()
        self.input_dir_edit.setPlaceholderText(default_input)
        form.addRow("input_dir", self._browse_row(self.input_dir_edit))

        self.processed_dir_edit = QLineEdit()
        self.processed_dir_edit.setPlaceholderText(default_processed)
        form.addRow("processed_dir", self._browse_row(self.processed_dir_edit))

        self.output_dir_edit = QLineEdit()
        self.output_dir_edit.setPlaceholderText(default_output)
        form.addRow("output_dir", self._browse_row(self.output_dir_edit))

        return box

    def _browse_row(self, line_edit: QLineEdit) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(line_edit)
        browse_btn = QPushButton("찾아보기")
        browse_btn.clicked.connect(lambda: self._browse_dir(line_edit))
        layout.addWidget(browse_btn)
        return row

    def _build_tesseract_box(self) -> QWidget:
        box = QGroupBox("Tesseract 상태")
        form = QFormLayout(box)
        self.tesseract_status_label = QLabel("")
        form.addRow("경로", self.tesseract_status_label)
        return box

    # -- field <-> cfg sync ----------------------------------------------------

    def _load_from_cfg(self) -> None:
        self.notion_api_key_edit.setText(self.cfg.notion_api_key)
        self.notion_database_id_edit.setText(self.cfg.notion_database_id)
        self.drive_folder_name_edit.setText(self.cfg.drive_folder_name)
        self.ocr_lang_edit.setText(self.cfg.ocr_lang)
        self.input_dir_edit.setText(self.cfg.input_dir)
        self.processed_dir_edit.setText(self.cfg.processed_dir)
        self.output_dir_edit.setText(self.cfg.output_dir)

    def _toggle_api_key_visibility(self, checked: bool) -> None:
        self.notion_api_key_edit.setEchoMode(
            QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        )
        self.notion_api_key_toggle.setText("숨기기" if checked else "표시")

    def _browse_dir(self, line_edit: QLineEdit) -> None:
        folder = QFileDialog.getExistingDirectory(self, "폴더 선택", line_edit.text() or str(Path.home()))
        if folder:
            line_edit.setText(folder)

    def _save(self) -> None:
        self.cfg.notion_api_key = self.notion_api_key_edit.text()
        self.cfg.notion_database_id = self.notion_database_id_edit.text().strip()
        self.cfg.drive_folder_name = self.drive_folder_name_edit.text().strip() or "mdconversion"
        self.cfg.ocr_lang = self.ocr_lang_edit.text().strip() or "eng+kor"
        self.cfg.input_dir = self.input_dir_edit.text().strip()
        self.cfg.processed_dir = self.processed_dir_edit.text().strip()
        self.cfg.output_dir = self.output_dir_edit.text().strip()

        config_mod.save_config(self.cfg)
        self.main_window.append_log("설정을 저장했습니다.")  # never log the key itself
        self.main_window.notify_config_changed()
        QMessageBox.information(self, "저장 완료", "설정을 저장했습니다.")

    # -- job-running gate --------------------------------------------------------

    def _refresh_job_running_state(self) -> None:
        """Disable 저장 while a 변환/동기화 점검 job is running so an edit
        made mid-run doesn't silently fail to apply to the run in progress
        (each worker snapshots its config at start -- see gui/workers.py)."""
        running = self.main_window.is_job_running
        self.save_btn.setEnabled(not running)
        self.job_running_label.setVisible(running)

    # -- credentials import --------------------------------------------------------

    def _refresh_credentials_status(self) -> None:
        exists = config_mod.credentials_path().exists()
        self.credentials_status_label.setText("등록됨" if exists else "등록되지 않음")

    def _import_credentials(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(self, "credentials.json 선택", "", "JSON 파일 (*.json)")
        if not file_path:
            return
        dest = config_mod.credentials_path()
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(file_path, dest)
        except OSError as exc:
            QMessageBox.warning(self, "가져오기 실패", f"credentials.json을 복사하지 못했습니다: {exc}")
            return

        self.main_window.append_log("credentials.json을 등록했습니다.")
        self._refresh_credentials_status()
        self.main_window.notify_config_changed()

    # -- legacy folder import --------------------------------------------------------

    def _import_legacy_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "기존 app 폴더 선택")
        if not folder:
            return

        src_dir = Path(folder)
        dest_dir = paths.user_data_dir()
        if src_dir.resolve() == dest_dir.resolve():
            QMessageBox.information(self, "가져오기", "선택한 폴더가 이미 현재 데이터 폴더와 동일합니다.")
            return

        available = [name for name in _LEGACY_FILES if (src_dir / name).exists()]
        if not available:
            QMessageBox.information(self, "가져오기", "선택한 폴더에서 가져올 파일을 찾지 못했습니다.")
            return

        overwrite = [name for name in available if (dest_dir / name).exists()]
        if overwrite:
            reply = QMessageBox.question(
                self,
                "덮어쓰기 확인",
                "다음 파일을 덮어씁니다:\n" + "\n".join(overwrite) + "\n\n계속하시겠습니까?",
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        copied = []
        failed = []
        for name in available:
            try:
                shutil.copy2(src_dir / name, dest_dir / name)
                copied.append(name)
            except OSError as exc:
                failed.append(f"{name} ({exc})")

        if copied:
            self.main_window.append_log(f"기존 app 폴더에서 가져옴: {', '.join(copied)}")
            if "config.json" in copied:
                self._reload_cfg_in_place()
                self._load_from_cfg()
            self._refresh_credentials_status()
            self.main_window.notify_config_changed()

        report = "복사된 파일:\n" + ("\n".join(copied) if copied else "(없음)")
        if failed:
            report += "\n\n실패한 파일:\n" + "\n".join(failed)
        QMessageBox.information(self, "가져오기 결과", report)

    def _reload_cfg_in_place(self) -> None:
        """Re-read config.json into the shared AppConfig instance, in place.

        Other tabs hold a reference to ``main_window.cfg`` -- replacing the
        object would leave them stale, so fields are copied onto the
        existing instance instead.
        """
        new_cfg = config_mod.load_config()
        for f in fields(config_mod.AppConfig):
            setattr(self.cfg, f.name, getattr(new_cfg, f.name))

    # -- tesseract status --------------------------------------------------------

    def _refresh_tesseract_status(self) -> None:
        engine = get_ocr_engine("tesseract")
        if engine.is_available():
            self.tesseract_status_label.setText(str(engine.binary_path()))
        else:
            self.tesseract_status_label.setText("찾을 수 없습니다")
