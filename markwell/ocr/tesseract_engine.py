"""Tesseract OCR backend.

Resolves the tesseract binary from a portable bundle, a system PATH install,
or the legacy hardcoded location (in that order), and manages a writable
copy of the ``tessdata_best`` language models so config.example.json's
languages can be downloaded on demand without touching a read-only install.
"""

import os
import shutil
import urllib.request
from pathlib import Path
from typing import Callable, Optional

from ..paths import bundle_dir, resource_path, user_data_dir
from .base import OcrEngine, OcrUnavailableError

TESSDATA_BASE_URL = "https://github.com/tesseract-ocr/tessdata_best/raw/main"
REQUIRED_EXTRA_LANGS = {"osd"}


def _noop(message: str) -> None:
    return None


class TesseractOcrEngine(OcrEngine):
    """OCR backend that shells out to Tesseract via pytesseract."""

    name = "tesseract"

    def __init__(self) -> None:
        self._binary_path: Optional[Path] = self._resolve_binary()
        self._tessdata_dir: Optional[Path] = None
        self._configured_langs: set = set()

        if self._binary_path is not None:
            import pytesseract

            pytesseract.pytesseract.tesseract_cmd = str(self._binary_path)

    # -- binary resolution -------------------------------------------------

    @staticmethod
    def _resolve_binary() -> Optional[Path]:
        """Find the tesseract executable.

        Resolution order:
        1. The portable bundle under ``resources/tesseract`` (vendored later;
           may not exist yet -- degrade gracefully).
        2. A system install on PATH.
        3. The legacy hardcoded install location, so today's working setup
           keeps working unchanged.
        """
        bundled = resource_path("tesseract", "tesseract.exe")
        if bundled.exists():
            return bundled

        on_path = shutil.which("tesseract")
        if on_path:
            return Path(on_path)

        legacy = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
        if legacy.exists():
            return legacy

        return None

    def is_available(self) -> bool:
        """Whether a usable tesseract binary was found."""
        return self._binary_path is not None

    def binary_path(self) -> Optional[Path]:
        """Path to the resolved tesseract binary, or ``None`` if unresolved."""
        return self._binary_path

    # -- tessdata management -------------------------------------------------

    def _writable_tessdata_dir(self) -> Path:
        """The writable tessdata directory, seeding it from bundled/legacy copies once."""
        if self._tessdata_dir is not None:
            return self._tessdata_dir

        target = user_data_dir() / "tessdata_best"
        target.mkdir(parents=True, exist_ok=True)

        # Seed order: portable bundle resource first, then the legacy
        # in-repo copy (app/tessdata_best) so existing committed eng/kor/osd
        # models are picked up without a re-download when running from source.
        seed_dirs = [resource_path("tessdata_best"), bundle_dir() / "app" / "tessdata_best"]
        for seed_dir in seed_dirs:
            if not seed_dir.exists() or not seed_dir.is_dir():
                continue
            for model_file in seed_dir.glob("*.traineddata"):
                dest = target / model_file.name
                if not dest.exists():
                    try:
                        shutil.copy2(model_file, dest)
                    except OSError:
                        pass

        self._tessdata_dir = target
        return target

    def _ensure_langs(self, ocr_lang: str, log: Callable[[str], None]) -> None:
        """Download any missing language models into the writable tessdata dir."""
        tessdata_dir = self._writable_tessdata_dir()

        langs = set(ocr_lang.split("+")) | REQUIRED_EXTRA_LANGS
        needed = langs - self._configured_langs
        if not needed:
            # Still make sure TESSDATA_PREFIX is set (idempotent).
            os.environ["TESSDATA_PREFIX"] = str(tessdata_dir)
            return

        for lang in sorted(needed):
            model_file = tessdata_dir / f"{lang}.traineddata"
            if model_file.exists():
                continue
            log(f"      -> [OCR] 언어 모델 '{lang}' 다운로드 중 (High Accuracy)...")
            url = f"{TESSDATA_BASE_URL}/{lang}.traineddata"
            try:
                urllib.request.urlretrieve(url, model_file)
            except Exception as exc:
                log(f"      -> [OCR Error] '{lang}' 모델 다운로드 실패: {exc}")

        # Use environment variable, not --tessdata-dir on the CLI: Windows
        # path quoting mangles the CLI argument (hard-won lesson from the
        # legacy converter.py). TESSDATA_PREFIX is the reliable path.
        os.environ["TESSDATA_PREFIX"] = str(tessdata_dir)
        self._configured_langs |= needed

    # -- OcrEngine interface --------------------------------------------------

    def ocr_image(self, image: "PIL.Image.Image", lang: str) -> str:
        if not self.is_available():
            raise OcrUnavailableError("Tesseract 실행 파일을 찾을 수 없습니다 (portable/PATH/legacy 모두 실패).")

        self._ensure_langs(lang, _noop)

        import pytesseract

        return pytesseract.image_to_string(image, lang=lang)

    def ocr_pdf_pages(self, pdf_path, page_numbers, lang, dpi=300, log=_noop, should_cancel=None):
        if not self.is_available():
            # Unavailability must be loud: returning {} here used to make an
            # OCR-requiring PDF look like a successful-but-empty conversion
            # further up the chain. Raise instead so the caller can fall back
            # or fail honestly.
            log("      -> [OCR Error] Tesseract 실행 파일을 찾을 수 없습니다 (portable/PATH/legacy 모두 실패).")
            raise OcrUnavailableError(
                "Tesseract 실행 파일을 찾을 수 없습니다 (portable/PATH/legacy 모두 실패)."
            )

        self._ensure_langs(lang, log)
        return super().ocr_pdf_pages(
            pdf_path, page_numbers, lang, dpi=dpi, log=log, should_cancel=should_cancel
        )
