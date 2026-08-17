"""Abstract OCR engine interface shared by all OCR backends.

``OcrEngine`` subclasses only need to implement :meth:`ocr_image` -- a single
PIL image to text. The default :meth:`ocr_pdf_pages` handles opening the PDF,
rasterizing the requested pages at a given DPI, and assembling the results,
so a future engine (e.g. EasyOCR) can be added with minimal code.
"""

import io
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Callable, Optional, Sequence, Union


def _noop(message: str) -> None:
    """Default no-op logger."""
    return None


class OcrUnavailableError(RuntimeError):
    """Raised when an OCR engine cannot run at all (e.g. no binary resolved).

    A subclass of RuntimeError so existing ``except Exception``/``except
    RuntimeError`` call sites keep working unchanged, while callers that need
    to tell "OCR is simply not installed" apart from "OCR ran and failed" can
    catch this specifically.
    """


class OcrCancelled(Exception):
    """Raised by :meth:`OcrEngine.ocr_pdf_pages` when ``should_cancel()`` fires.

    Carries whatever pages were completed before the cancellation in
    ``partial_results`` (``{0-indexed page: text}``) so a caller that wants
    the partial output can still get at it -- but the exception itself makes
    the run impossible to mistake for a completed one.
    """

    def __init__(self, partial_results: dict):
        pages_done = len(partial_results)
        super().__init__(f"OCR이 사용자 요청으로 중단되었습니다 ({pages_done}페이지 처리 완료).")
        self.partial_results = partial_results


class OcrEngine(ABC):
    """Base class for OCR backends used by the PDF conversion engine."""

    name: str

    @abstractmethod
    def ocr_image(self, image: "PIL.Image.Image", lang: str) -> str:
        """Run OCR on a single in-memory image and return extracted text.

        Args:
            image: A PIL image (already rasterized from a PDF page or loaded
                directly).
            lang: Language code(s) understood by the engine (e.g. ``"eng+kor"``).

        Returns:
            Extracted text, or an empty string if nothing was recognized.
        """
        raise NotImplementedError

    def ocr_pdf_pages(
        self,
        pdf_path: Union[str, Path],
        page_numbers: Optional[Sequence[int]],
        lang: str,
        dpi: int = 300,
        log: Callable[[str], None] = _noop,
        should_cancel: Optional[Callable[[], bool]] = None,
    ) -> dict:
        """Rasterize and OCR the requested pages of a PDF.

        Args:
            pdf_path: Path to the PDF file. Always ``str()``-cast before use.
            page_numbers: 0-indexed page numbers to OCR. ``None`` means every
                page in the document.
            lang: Language code(s) passed through to :meth:`ocr_image`.
            dpi: Rasterization resolution.
            log: Callback for per-page progress lines.
            should_cancel: Optional zero-arg callable checked between pages;
                if it returns True, OCR stops early and raises
                :class:`OcrCancelled` (carrying the pages done so far)
                instead of returning a partial result the caller would have
                no way to distinguish from a complete run.

        Returns:
            Mapping of ``{0-indexed page number: extracted text}``. Pages
            that failed to OCR map to an empty string rather than being
            omitted.

        Raises:
            OcrCancelled: ``should_cancel()`` returned True before every
                requested page was processed.
        """
        import fitz  # PyMuPDF -- imported lazily so a missing install fails late
        from PIL import Image

        results: dict = {}
        doc = fitz.open(str(pdf_path))
        try:
            total_pages = doc.page_count
            targets = list(page_numbers) if page_numbers is not None else list(range(total_pages))

            for idx, page_no in enumerate(targets):
                if should_cancel is not None and should_cancel():
                    log(f"      -> [OCR] 사용자 요청으로 중단됨 ({idx}/{len(targets)} 페이지 처리 완료)")
                    raise OcrCancelled(dict(results))

                try:
                    page = doc[page_no]
                    pix = page.get_pixmap(dpi=dpi)
                    img_data = pix.tobytes("png")
                    image = Image.open(io.BytesIO(img_data))
                    text = self.ocr_image(image, lang)
                    results[page_no] = text
                    log(f"      -> [OCR] {page_no + 1}페이지 처리 완료 ({idx + 1}/{len(targets)})")
                except Exception as exc:  # one bad page must not abort the document
                    results[page_no] = ""
                    log(f"      -> [OCR Error] {page_no + 1}페이지 OCR 실패: {exc}")
        finally:
            doc.close()

        failed_count = sum(1 for text in results.values() if not (text or "").strip())
        if failed_count:
            log(
                f"      -> [OCR] {failed_count}/{len(results)}페이지에서 텍스트를 인식하지 못했습니다."
            )

        return results
