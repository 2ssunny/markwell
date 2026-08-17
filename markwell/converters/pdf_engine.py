"""PDF conversion engine built on pdf-inspector, with OCR for scanned/mixed pages.

pdf_inspector.process_pdf() is called exactly once per document and the
result is branched on ``pdf_type``:

* ``text_based``  -- use the extracted markdown as-is, no OCR.
* ``scanned`` / ``image_based`` -- OCR every page.
* ``mixed``       -- the main optimization: only OCR the pages pdf_inspector
  flagged as needing it; text pages are used verbatim with no rasterization.

A last-resort safety net re-OCRs the whole document if the assembled
markdown ends up suspiciously short, regardless of what pdf_type said.
"""

import re
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple, Union

from . import ConversionResult
from ..ocr.base import OcrEngine

ENGINE_NAME = "pdf-inspector"
MIN_TEXT_LENGTH = 50  # last-resort guard threshold, not the primary OCR trigger

_OCR_MARKER_RE = re.compile(r"<!--\s*Page \d+: OCR\s*-->")


def _noop(message: str) -> None:
    return None


def visible_text_length(markdown: str) -> int:
    """Length of ``markdown`` with ``<!-- Page N: OCR -->`` markers stripped out.

    Marker comments are structural, not recognized content -- a document
    where every OCR page came back empty must not look "long enough" just
    because its markers are still there. Use this for every length/emptiness
    decision downstream instead of ``len(markdown.strip())``.
    """
    if not markdown:
        return 0
    return len(_OCR_MARKER_RE.sub("", markdown).strip())


def join_ocr_pages(page_texts: Dict[int, str]) -> str:
    """Join ``{0-indexed page: text}`` into markdown, in ascending page order.

    Each page with recognized text is prefixed with an
    ``<!-- Page N: OCR -->`` marker using the human-readable 1-based page
    number. Pages whose OCR produced no text are omitted entirely -- a bare
    marker with nothing under it must never make an empty OCR run look like
    it produced content.
    """
    parts = []
    for page_no in sorted(page_texts.keys()):
        text = (page_texts[page_no] or "").strip()
        if not text:
            continue
        parts.append(f"<!-- Page {page_no + 1}: OCR -->\n\n{text}")
    return "\n\n".join(parts).strip()


def convert(
    path: Union[str, Path],
    ocr_lang: str,
    ocr_engine: OcrEngine,
    log: Callable[[str], None] = _noop,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> ConversionResult:
    """Convert a PDF to Markdown, branching on pdf-inspector's classification.

    Args:
        path: PDF file to convert.
        ocr_lang: Tesseract-style language string (e.g. ``"eng+kor"``).
        ocr_engine: OCR backend used for scanned/mixed pages.
        log: Progress/log callback.
        should_cancel: Optional zero-arg callable to abort a long OCR run early.

    Returns:
        ConversionResult with markdown, the winning engine name, and the
        number of OCR'd pages.

    Raises:
        ValueError: any pdf_inspector failure (corrupt/empty/non-PDF file).
            Left to propagate -- dispatch.py owns the engine-level fallback.
        ImportError: pdf_inspector is not importable (e.g. a broken bundle).
            Also left to propagate -- dispatch.py catches broadly and falls
            back to markitdown.
        OcrUnavailableError: the OCR engine could not run at all (e.g. no
            Tesseract binary resolved). Propagates from ``ocr_engine``.
        OcrCancelled: ``should_cancel()`` fired during an OCR pass. Left to
            propagate untouched -- a cancelled run must never be caught and
            treated as "this attempt failed, try the next fallback".
    """
    import pdf_inspector

    path_str = str(path)  # pdf_inspector accepts str only; a Path raises TypeError
    result = pdf_inspector.process_pdf(path_str)

    notes = []
    ocr_pages_count = 0
    engine_name = ENGINE_NAME
    pdf_type = result.pdf_type

    full_document_ocr_done = False

    if pdf_type == "text_based":
        log(f"      -> [pdf-inspector] 'text_based' 유형 ({result.page_count}페이지) -> OCR 없이 텍스트 추출")
        markdown = result.markdown or ""
        if getattr(result, "has_encoding_issues", False):
            msg = "인코딩 문제가 감지되었지만 추출된 텍스트를 그대로 사용합니다."
            log(f"      -> [pdf-inspector] 경고: {msg}")
            notes.append(msg)

    elif pdf_type in ("scanned", "image_based"):
        log(f"      -> [pdf-inspector] '{pdf_type}' 유형 감지 -> 전체 {result.page_count}페이지 OCR 수행")
        page_texts = ocr_engine.ocr_pdf_pages(
            path_str, None, ocr_lang, log=log, should_cancel=should_cancel
        )
        ocr_pages_count = len(page_texts)
        markdown = join_ocr_pages(page_texts)
        engine_name = f"{ocr_engine.name}-ocr"
        full_document_ocr_done = True

    elif pdf_type == "mixed":
        markdown, ocr_pages_count = _convert_mixed(path_str, ocr_lang, ocr_engine, log, should_cancel)
        engine_name = f"{ENGINE_NAME}+{ocr_engine.name}-ocr" if ocr_pages_count else ENGINE_NAME

    else:
        # Unexpected pdf_type from a future pdf_inspector version: fall back
        # to whatever markdown it produced rather than failing outright.
        markdown = result.markdown or ""
        notes.append(f"알 수 없는 pdf_type '{pdf_type}' -- 기본 markdown을 사용했습니다.")

    # Safety net: demoted from primary OCR trigger to last-resort guard.
    # Skipped when every page has already been OCR'd -- re-running it would just
    # spend the same minutes to reach the same empty result (a genuinely blank
    # scan stays blank).
    if not full_document_ocr_done and visible_text_length(markdown) < MIN_TEXT_LENGTH:
        log("      -> [pdf-inspector] 추출된 텍스트가 부족합니다 (safety net) -> 전체 페이지 OCR 재시도")
        fallback_pages = ocr_engine.ocr_pdf_pages(
            path_str, None, ocr_lang, log=log, should_cancel=should_cancel
        )
        fallback_markdown = join_ocr_pages(fallback_pages)
        if visible_text_length(fallback_markdown) > visible_text_length(markdown):
            markdown = fallback_markdown
            ocr_pages_count = len(fallback_pages)
            engine_name = f"{ocr_engine.name}-ocr"
            notes.append("안전망(safety net) 전체 페이지 OCR 결과를 사용했습니다.")

    return ConversionResult(markdown=markdown, engine=engine_name, ocr_pages=ocr_pages_count, notes=notes)


def _convert_mixed(
    path_str: str,
    ocr_lang: str,
    ocr_engine: OcrEngine,
    log: Callable[[str], None],
    should_cancel: Optional[Callable[[], bool]],
) -> Tuple[str, int]:
    """Merge text pages verbatim with OCR only for the pages that need it.

    ``PageMarkdown.page`` is 0-indexed and is the single source of truth used
    for merging here (as opposed to the 1-indexed ``pages_needing_ocr`` lists
    elsewhere in the pdf_inspector API).
    """
    import pdf_inspector

    extraction = pdf_inspector.extract_pages_markdown(path_str)
    pages = extraction.pages

    ocr_page_numbers = [p.page for p in pages if p.needs_ocr]  # 0-indexed
    ocr_texts: Dict[int, str] = {}
    if ocr_page_numbers:
        ocr_texts = ocr_engine.ocr_pdf_pages(
            path_str, ocr_page_numbers, ocr_lang, log=log, should_cancel=should_cancel
        )

    parts = []
    text_page_count = 0
    for page in sorted(pages, key=lambda p: p.page):
        if page.needs_ocr:
            ocr_text = (ocr_texts.get(page.page, "") or "").strip()
            if ocr_text:
                parts.append(f"<!-- Page {page.page + 1}: OCR -->\n\n{ocr_text}")
            # else: OCR produced no text for this page -- omit it rather than
            # emitting a bare marker that would count as content downstream.
        else:
            parts.append(page.markdown or "")
            text_page_count += 1

    log(
        f"      -> [pdf-inspector] mixed 문서 병합 완료: 텍스트 페이지 {text_page_count}개 그대로 사용, "
        f"OCR 페이지 {len(ocr_page_numbers)}개 (총 {len(pages)}페이지)"
    )

    return "\n\n".join(parts).strip(), len(ocr_page_numbers)
