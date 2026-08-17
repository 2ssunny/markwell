"""Shared fixtures and test helpers for the markwell test suite.

Two things happen here that every other test file relies on:

1. ``QT_QPA_PLATFORM=offscreen`` is set before anything in this process gets a
   chance to import PySide6, so the suite runs headless on a CI runner with no
   display attached.
2. An autouse fixture redirects ``markwell.paths.user_data_dir()`` (and
   ``default_workspace_dir()``) to a throwaway ``tmp_path`` for *every* test,
   so nothing in this suite can accidentally read or write the real
   ``<repo>/app/{config,credentials,token,history}.json``. Tests that need to
   exercise ``markwell.paths``' own resolution logic (the legacy-migration
   tests) opt out explicitly with ``@pytest.mark.real_paths`` and drive the
   real function through a faked ``LOCALAPPDATA`` instead.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import time
from pathlib import Path
from typing import Callable, Dict, Optional, Sequence

import pytest


# ---------------------------------------------------------------------------
# User-state isolation (the single most important fixture in this suite)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_user_data(request, tmp_path, monkeypatch):
    if "real_paths" in request.keywords:
        # test_migration.py: it IS the test of paths.user_data_dir()'s own
        # resolution logic, so it drives the real function via a faked
        # LOCALAPPDATA + is_frozen() instead of stubbing the function away.
        yield
        return

    data_dir = tmp_path / "user_data"
    data_dir.mkdir()
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()

    monkeypatch.setattr("markwell.paths.user_data_dir", lambda: data_dir)
    monkeypatch.setattr("markwell.paths.default_workspace_dir", lambda: workspace_dir)

    # markwell.ocr.tesseract_engine did `from ..paths import user_data_dir`,
    # binding the function directly into its own namespace -- patching the
    # attribute on markwell.paths above does NOT affect that already-bound
    # reference. Without this second patch, any test that ends up running
    # real OCR (e.g. a PDF fallback chain reaching the OCR step) would seed/
    # download tessdata into the real <repo>/app/tessdata_best.
    monkeypatch.setattr("markwell.ocr.tesseract_engine.user_data_dir", lambda: data_dir)

    yield


# ---------------------------------------------------------------------------
# Qt / GUI fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def qapp():
    """A single QApplication for the whole session (Qt forbids more than one)."""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def pump_events_until(condition: Callable[[], bool], timeout: float = 5.0, interval: float = 0.01) -> bool:
    """Spin the Qt event loop until ``condition()`` is true or ``timeout`` elapses.

    Returns whether the condition became true. Used instead of QTest.qWait
    (no pytest-qt dependency) to observe cross-thread signal delivery and
    worker lifecycle transitions without hard-coded sleeps.
    """
    from PySide6.QtWidgets import QApplication

    deadline = time.monotonic() + timeout
    while True:
        QApplication.processEvents()
        if condition():
            return True
        if time.monotonic() >= deadline:
            return condition()
        time.sleep(interval)


def spin_events(seconds: float) -> None:
    """Process Qt events for a fixed wall-clock duration (no condition to wait for)."""
    from PySide6.QtWidgets import QApplication

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.005)


# The offscreen QPA plugin has no real font directory to find and logs a
# QFontDatabase warning about it on every run, regardless of anything this
# app does -- it is CI/headless-environment noise, not a signal about app
# correctness (unlike e.g. "QThread: Destroyed while thread is still
# running", which IS a real bug signal and must never be filtered out).
_BENIGN_QT_MESSAGE_MARKERS = ("QFontDatabase",)


def significant_qt_messages(messages):
    """``messages`` with known-benign offscreen-platform noise filtered out."""
    return [m for m in messages if not any(marker in m for marker in _BENIGN_QT_MESSAGE_MARKERS)]


@pytest.fixture
def main_window(qapp):
    """A constructed ``MainWindow`` against the isolated tmp user-data dir.

    Teardown best-effort stops any worker so a leftover background QThread
    never outlives the test (which is exactly the crash workers.py's
    docstring warns about).
    """
    from markwell.gui.main_window import MainWindow

    window = MainWindow()
    yield window
    try:
        window.convert_tab.shutdown()
        window.sync_check_tab.shutdown()
    except Exception:
        pass
    window.close()
    spin_events(0.05)


# ---------------------------------------------------------------------------
# Synthetic fixture files -- never read from processed_files/input_files/app
# ---------------------------------------------------------------------------


def write_text_pdf(path: Path, text: Optional[str] = None) -> Path:
    """A genuinely text-based single-page PDF (pdf-inspector classifies it
    ``text_based``, no OCR involved)."""
    import fitz  # PyMuPDF

    if text is None:
        text = "Synthetic text PDF content for automated testing. " * 6

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    doc.save(str(path))
    doc.close()
    return path


def write_corrupt_pdf(path: Path) -> Path:
    """A valid PDF header followed by garbage -- pdf-inspector/fitz both reject it."""
    import os as _os

    path.write_bytes(b"%PDF-1.4\n" + _os.urandom(256))
    return path


def write_corrupt_docx(path: Path) -> Path:
    """Random bytes named .docx -- not a zip archive at all."""
    import os as _os

    path.write_bytes(_os.urandom(512))
    return path


def write_csv(path: Path, rows: Optional[Sequence[Sequence[str]]] = None) -> Path:
    if rows is None:
        rows = [["a", "b", "c"], ["1", "2", "3"]]
    path.write_text("\n".join(",".join(row) for row in rows) + "\n", encoding="utf-8")
    return path


def write_xlsx(path: Path, rows: Optional[Sequence[Sequence]] = None) -> Path:
    import openpyxl

    if rows is None:
        rows = [["a", "b", "c"], [1, 2, 3]]
    wb = openpyxl.Workbook()
    ws = wb.active
    for row in rows:
        ws.append(list(row))
    wb.save(str(path))
    return path


def write_txt(path: Path, text: str = "hello there, a plain text fixture file") -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def write_html(path: Path, title: str = "Title", body: str = "Body text here") -> Path:
    path.write_text(f"<html><body><h1>{title}</h1><p>{body}</p></body></html>", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Fake OCR engine -- keeps test_dispatch.py/test_pipeline.py independent of a
# real Tesseract install and, crucially, never touches any tessdata directory.
# ---------------------------------------------------------------------------


def _make_fake_ocr_engine():
    from markwell.ocr.base import OcrEngine

    class FakeOcrEngine(OcrEngine):
        name = "fake"

        def __init__(self, page_text: Optional[Dict[int, str]] = None, fail: bool = False):
            self._page_text = dict(page_text) if page_text else {}
            self._fail = fail
            self.calls = []

        def ocr_image(self, image, lang):  # pragma: no cover - not exercised
            raise NotImplementedError("FakeOcrEngine.ocr_image should never be called by these tests")

        def ocr_pdf_pages(self, pdf_path, page_numbers, lang, dpi=300, log=lambda m: None, should_cancel=None):
            self.calls.append((str(pdf_path), page_numbers, lang))
            if self._fail:
                raise RuntimeError("FakeOcrEngine: simulated OCR failure (no real Tesseract used in tests)")
            return dict(self._page_text)

    return FakeOcrEngine


FakeOcrEngine = _make_fake_ocr_engine()


@pytest.fixture
def fake_ocr_engine():
    """A fresh non-failing FakeOcrEngine instance per test."""
    return FakeOcrEngine()


# ---------------------------------------------------------------------------
# AppConfig factory
# ---------------------------------------------------------------------------


def make_cfg(tmp_path: Path, **overrides):
    from markwell.config import AppConfig

    kwargs = dict(
        enable_drive=False,
        enable_notion=False,
        input_dir=str(tmp_path / "input_files"),
        processed_dir=str(tmp_path / "processed_files"),
        output_dir=str(tmp_path / "output_files"),
    )
    kwargs.update(overrides)
    return AppConfig(**kwargs)
