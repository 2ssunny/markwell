"""GUI entry point.

Works both as ``python -m markwell.gui.app`` (development) and as the
PyInstaller entry point for the frozen windowed build. A windowed build has
no console, so uncaught exceptions are routed into a QMessageBox instead of
crashing silently.
"""

import sys
import traceback

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from .. import paths
from .main_window import MainWindow


def _install_excepthook() -> None:
    def handle(exc_type, exc_value, exc_tb):
        message = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        sys.stderr.write(message)

        box = QMessageBox()
        box.setIcon(QMessageBox.Icon.Critical)
        box.setWindowTitle("예상치 못한 오류")
        box.setText("예상치 못한 오류가 발생했습니다.")
        box.setDetailedText(message)
        box.exec()

    sys.excepthook = handle


def main() -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    _install_excepthook()

    icon_path = paths.resource_path("icon.ico")
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    window = MainWindow()
    if icon_path.exists():
        window.setWindowIcon(QIcon(str(icon_path)))
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
