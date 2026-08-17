"""markwell/dispatch.py: extension routing, is_supported(), and the
per-extension fallback chains.

A FakeOcrEngine stands in for Tesseract everywhere here -- none of these
behaviours need real OCR pixels, and using the real TesseractOcrEngine would
either require a Tesseract install on CI or (worse) actually touch a tessdata
directory the moment a fallback chain reaches the OCR step.
"""

from pathlib import Path

import pytest

from markwell import dispatch

from .conftest import (
    FakeOcrEngine,
    write_corrupt_docx,
    write_corrupt_pdf,
    write_csv,
    write_html,
    write_text_pdf,
    write_txt,
    write_xlsx,
)


def test_text_pdf_routes_to_pdf_inspector_with_no_ocr(tmp_path):
    pdf_path = write_text_pdf(tmp_path / "report.pdf")
    engine = FakeOcrEngine()

    result = dispatch.convert_file(pdf_path, ocr_lang="eng", ocr_engine=engine)

    assert result.engine == "pdf-inspector"
    assert result.ocr_pages == 0
    assert "Synthetic text PDF content" in result.markdown
    assert engine.calls == []  # OCR must never be invoked for a text-based PDF


def test_csv_routes_to_anydoc(tmp_path):
    csv_path = write_csv(tmp_path / "data.csv", rows=[["a", "b"], ["1", "2"]])
    engine = FakeOcrEngine()

    result = dispatch.convert_file(csv_path, ocr_lang="eng", ocr_engine=engine)

    assert result.engine == "anydoc"
    assert "1" in result.markdown and "2" in result.markdown


def test_xlsx_routes_to_anydoc(tmp_path):
    xlsx_path = write_xlsx(tmp_path / "data.xlsx", rows=[["a", "b"], [1, 2]])
    engine = FakeOcrEngine()

    result = dispatch.convert_file(xlsx_path, ocr_lang="eng", ocr_engine=engine)

    assert result.engine == "anydoc"


def test_txt_routes_to_markitdown(tmp_path):
    txt_path = write_txt(tmp_path / "note.txt", "hello there general text file")
    engine = FakeOcrEngine()

    result = dispatch.convert_file(txt_path, ocr_lang="eng", ocr_engine=engine)

    assert result.engine == "markitdown"
    assert "hello there general text file" in result.markdown


def test_html_routes_to_markitdown(tmp_path):
    html_path = write_html(tmp_path / "page.html", title="Title", body="Body text here")
    engine = FakeOcrEngine()

    result = dispatch.convert_file(html_path, ocr_lang="eng", ocr_engine=engine)

    assert result.engine == "markitdown"
    assert "Body text here" in result.markdown


@pytest.mark.parametrize(
    "ext",
    sorted(dispatch.PDF_EXTENSIONS | dispatch.ANYDOC_EXTENSIONS | dispatch.MARKITDOWN_EXTENSIONS),
)
def test_is_supported_accepts_every_documented_extension(ext):
    assert dispatch.is_supported(f"whatever{ext}") is True


@pytest.mark.parametrize("ext", [".xyz", ".exe", ".unsupported", ""])
def test_is_supported_rejects_unknown_extensions(ext):
    assert dispatch.is_supported(f"whatever{ext}") is False


def test_corrupted_pdf_raises_clear_error_exhausting_full_fallback_chain(tmp_path):
    corrupt = write_corrupt_pdf(tmp_path / "corrupt.pdf")
    engine = FakeOcrEngine(fail=True)
    log_lines = []

    with pytest.raises(RuntimeError) as excinfo:
        dispatch.convert_file(corrupt, ocr_lang="eng", ocr_engine=engine, log=log_lines.append)

    message = str(excinfo.value)
    # A clear, file-scoped error -- not an opaque traceback from pdf_inspector/fitz.
    assert "corrupt.pdf" in message
    assert "pdf-inspector" in message
    assert "markitdown" in message
    assert "OCR" in message
    # The fallback chain was actually exercised: pdf-inspector and markitdown
    # both got a real attempt (and logged their own failure) before OCR did.
    joined_log = "\n".join(log_lines)
    assert "pdf-inspector" in joined_log
    assert "markitdown" in joined_log
    assert "OCR" in joined_log


def test_corrupted_docx_raises_clear_error_exhausting_full_fallback_chain(tmp_path):
    corrupt = write_corrupt_docx(tmp_path / "corrupt.docx")
    engine = FakeOcrEngine()  # unused for anydoc-family extensions
    log_lines = []

    with pytest.raises(RuntimeError) as excinfo:
        dispatch.convert_file(corrupt, ocr_lang="eng", ocr_engine=engine, log=log_lines.append)

    message = str(excinfo.value)
    assert "corrupt.docx" in message
    assert "anydoc" in message
    assert "markitdown" in message
    # .docx has a markitdown fallback (MARKITDOWN_FALLBACK_EXTENSIONS), so both
    # engines must have been tried, not just anydoc failing outright.
    joined_log = "\n".join(log_lines)
    assert "anydoc" in joined_log
    assert "markitdown" in joined_log


def test_unsupported_extension_raises_unsupported_file_error(tmp_path):
    mystery = tmp_path / "mystery.xyz"
    mystery.write_text("nothing engine understands", encoding="utf-8")
    engine = FakeOcrEngine()

    with pytest.raises(dispatch.UnsupportedFileError):
        dispatch.convert_file(mystery, ocr_lang="eng", ocr_engine=engine)


def test_pathlib_path_argument_converts_end_to_end_without_typeerror(tmp_path):
    """pdf_inspector.process_pdf() only accepts str -- a missed str() cast
    anywhere in the pdf path would surface as a TypeError here."""
    pdf_path = write_text_pdf(tmp_path / "path_arg.pdf")
    assert isinstance(pdf_path, Path)
    engine = FakeOcrEngine()

    result = dispatch.convert_file(pdf_path, ocr_lang="eng", ocr_engine=engine)

    assert result.engine == "pdf-inspector"


def test_pathlib_path_argument_works_for_anydoc_and_markitdown_too(tmp_path):
    csv_path = write_csv(tmp_path / "path_arg.csv")
    txt_path = write_txt(tmp_path / "path_arg.txt")
    engine = FakeOcrEngine()

    assert dispatch.convert_file(csv_path, ocr_lang="eng", ocr_engine=engine).engine == "anydoc"
    assert dispatch.convert_file(txt_path, ocr_lang="eng", ocr_engine=engine).engine == "markitdown"
