"""Dispatch layer: routes a file to the right conversion engine by extension
and owns every engine-level fallback chain.

Each ``converters/*_engine.py`` module raises on failure and lets this
module decide what to try next -- the engines themselves never fall back to
each other.
"""

from pathlib import Path
from typing import Callable, Dict, Optional, Union

from .converters import ConversionResult, anydoc_engine, markitdown_engine, pdf_engine
from .ocr.base import OcrCancelled, OcrEngine

PDF_EXTENSIONS = {'.pdf'}
ANYDOC_EXTENSIONS = {
    '.doc', '.docx', '.docm', '.ppt', '.pps', '.pot', '.pptx', '.pptm', '.ppsx', '.ppsm',
    '.xls', '.xlsx', '.xlsm', '.xlsb', '.odt', '.ods', '.odp', '.rtf', '.epub', '.csv',
}
MARKITDOWN_EXTENSIONS = {'.html', '.htm', '.zip', '.json', '.xml', '.txt', '.jpg', '.jpeg', '.png'}
SUPPORTED_EXTENSIONS = PDF_EXTENSIONS | ANYDOC_EXTENSIONS | MARKITDOWN_EXTENSIONS

# markitdown ships its own converters for these, so they have a real fallback.
# .doc/.ppt/.xls/.odt/.ods/.odp/.rtf have none -- verified against markitdown 0.1.6's converters/.
MARKITDOWN_FALLBACK_EXTENSIONS = {'.docx', '.pptx', '.xlsx', '.csv', '.epub'}

MIN_TEXT_LENGTH = 50  # last-resort guard threshold, shared with pdf_engine's own safety net


def _noop(message: str) -> None:
    return None


class UnsupportedFileError(Exception):
    """Raised when a file's extension is not handled by any conversion engine."""


def is_supported(path: Union[str, Path]) -> bool:
    """Whether ``path``'s extension is handled by any engine."""
    return Path(path).suffix.lower() in SUPPORTED_EXTENSIONS


def convert_file(
    path: Union[str, Path],
    *,
    ocr_lang: str,
    ocr_engine: OcrEngine,
    log: Callable[[str], None] = _noop,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> ConversionResult:
    """Convert ``path`` to Markdown, applying the extension-appropriate fallback chain.

    Fallback chains:
        PDF: pdf_engine -> on exception or a result with < 50 chars of
            actual recognized text (OCR page markers don't count),
            markitdown -> if markitdown yields < 50 chars (or also fails),
            full-document OCR as the last resort.
        anydoc extensions: anydoc_engine -> on any exception (ConvertError,
            OSError, ImportError, ...) or an empty result, markitdown if the
            extension is in MARKITDOWN_FALLBACK_EXTENSIONS, else a clear
            error is raised.
        markitdown extensions: markitdown only, no further fallback.

    A PDF whose OCR pass is cancelled mid-run (``should_cancel()`` fires)
    raises ``OcrCancelled`` straight through this function instead of
    silently falling back -- a cancelled run must never be mistaken for a
    completed or a failed one.

    Args:
        path: File to convert (str or Path).
        ocr_lang: Tesseract-style language string, e.g. ``"eng+kor"``.
        ocr_engine: OCR backend used for scanned/mixed PDF pages.
        log: Progress/log callback (Korean, user-facing).
        should_cancel: Optional zero-arg callable checked during long OCR runs.

    Returns:
        ConversionResult produced by whichever engine ultimately won.

    Raises:
        UnsupportedFileError: the extension is not handled by any engine.
        RuntimeError: every engine in the applicable fallback chain failed
            (including "succeeded but produced no meaningful text").
        OcrCancelled: (PDF only) ``should_cancel()`` fired during an OCR
            pass. Propagates untouched.
    """
    path = Path(path)
    ext = path.suffix.lower()

    if ext in PDF_EXTENSIONS:
        return _convert_pdf(path, ocr_lang, ocr_engine, log, should_cancel)
    if ext in ANYDOC_EXTENSIONS:
        return _convert_anydoc(path, ext, log)
    if ext in MARKITDOWN_EXTENSIONS:
        return _convert_markitdown_only(path, log)

    raise UnsupportedFileError(f"지원하지 않는 파일 형식입니다: '{path.name}' ({ext})")


def _convert_pdf(
    path: Path,
    ocr_lang: str,
    ocr_engine: OcrEngine,
    log: Callable[[str], None],
    should_cancel: Optional[Callable[[], bool]],
) -> ConversionResult:
    pdf_error: Optional[BaseException] = None
    try:
        result = pdf_engine.convert(path, ocr_lang, ocr_engine, log=log, should_cancel=should_cancel)
        if pdf_engine.visible_text_length(result.markdown) >= MIN_TEXT_LENGTH:
            return result
        # pdf_engine returned "successfully" but with no meaningful recognized
        # text (e.g. a scanned PDF where OCR ran but every page came back
        # empty) -- do not accept that as a completed conversion. Fall
        # through to markitdown/OCR like any other failure.
        pdf_error = RuntimeError("pdf-inspector가 의미 있는 텍스트를 추출하지 못했습니다.")
        log("      -> [pdf-inspector] 추출된 텍스트가 부족합니다 (50자 미만) -> markitdown으로 재시도")
    except OcrCancelled:
        # Cancellation must never look like "this attempt failed, try the
        # next fallback" -- propagate it untouched so the caller can tell
        # the run was aborted, not completed or failed outright.
        raise
    except Exception as exc:  # pdf_inspector raises ValueError for every failure mode; catch broadly regardless
        pdf_error = exc
        log(f"      -> [pdf-inspector] 실패 ({type(exc).__name__}: {exc}) -> markitdown으로 재시도")

    markitdown_error: Optional[BaseException] = None
    markdown = ""
    try:
        markdown = markitdown_engine.convert(path, log=log)
    except Exception as exc:
        markitdown_error = exc
        log(f"      -> [markitdown] 실패 ({type(exc).__name__}: {exc}) -> 전체 페이지 OCR로 재시도")

    if markitdown_error is None and len(markdown.strip()) >= MIN_TEXT_LENGTH:
        return ConversionResult(markdown=markdown, engine="markitdown")
    if markitdown_error is None:
        log("      -> [markitdown] 추출된 텍스트가 부족합니다 (50자 미만) -> 전체 페이지 OCR로 재시도")

    page_texts: Dict[int, str] = {}
    ocr_markdown = ""
    ocr_error: Optional[BaseException] = None
    try:
        page_texts = ocr_engine.ocr_pdf_pages(str(path), None, ocr_lang, log=log, should_cancel=should_cancel)
        ocr_markdown = pdf_engine.join_ocr_pages(page_texts)
    except OcrCancelled:
        raise
    except Exception as exc:
        ocr_error = exc
        log(f"      -> [OCR] 실패 ({type(exc).__name__}: {exc})")

    if pdf_engine.visible_text_length(ocr_markdown) == 0:
        last_error = ocr_error or markitdown_error or pdf_error
        raise RuntimeError(
            f"'{path.name}' 변환에 실패했습니다: pdf-inspector"
            f"({type(pdf_error).__name__ if pdf_error else '실패'}), markitdown"
            f"({type(markitdown_error).__name__ if markitdown_error else '텍스트 부족'}), OCR"
            f"({type(ocr_error).__name__ if ocr_error else '텍스트 없음'}) 모두 실패했습니다."
        ) from last_error

    return ConversionResult(
        markdown=ocr_markdown, engine=f"{ocr_engine.name}-ocr", ocr_pages=len(page_texts)
    )


def _convert_anydoc(path: Path, ext: str, log: Callable[[str], None]) -> ConversionResult:
    # Catch broadly, not just anydoc.ConvertError: the installed anydoc's own
    # docs say to_markdown() can also raise OSError when the file can't be
    # read, and importing anydoc itself can fail with ImportError in a
    # packaged build where the module didn't get bundled. anydoc_engine.convert()
    # does its own `import anydoc` internally, so keeping that import out of
    # this function (and inside the try below, transitively) means an
    # ImportError here falls through to the markitdown fallback instead of
    # aborting the file outright.
    anydoc_error: Optional[BaseException] = None
    markdown = ""
    try:
        markdown = anydoc_engine.convert(path, log=log)
    except Exception as exc:
        anydoc_error = exc
        log(f"      -> [anydoc] 실패 ({type(exc).__name__}: {exc})")

    if anydoc_error is None and markdown and markdown.strip():
        return ConversionResult(markdown=markdown, engine="anydoc")

    if anydoc_error is None:
        anydoc_error = RuntimeError("anydoc이 빈 결과를 반환했습니다.")

    if ext not in MARKITDOWN_FALLBACK_EXTENSIONS:
        raise RuntimeError(
            f"'{path.name}' 변환 실패 ({type(anydoc_error).__name__}): {anydoc_error}"
        ) from anydoc_error

    log(f"      -> anydoc 실패({type(anydoc_error).__name__}) → markitdown으로 재시도")
    try:
        markdown = markitdown_engine.convert(path, log=log)
    except Exception as exc:
        raise RuntimeError(
            f"'{path.name}' 변환 실패: anydoc({type(anydoc_error).__name__})과 "
            f"markitdown({type(exc).__name__}) 모두 실패했습니다."
        ) from exc

    if markdown and markdown.strip():
        return ConversionResult(markdown=markdown, engine="markitdown")

    raise RuntimeError(
        f"'{path.name}' 변환 실패: anydoc과 markitdown 모두 텍스트를 추출하지 못했습니다."
    ) from anydoc_error


def _convert_markitdown_only(path: Path, log: Callable[[str], None]) -> ConversionResult:
    markdown = markitdown_engine.convert(path, log=log)
    return ConversionResult(markdown=markdown, engine="markitdown")
