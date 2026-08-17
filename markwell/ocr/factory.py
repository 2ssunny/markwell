"""Registry/factory for OCR engines.

This module is the sole extension point for adding new OCR backends (e.g.
re-adding EasyOCR) -- register the class in ``_REGISTRY`` and it becomes
selectable by name.
"""

from typing import Callable, Dict

from .base import OcrEngine
from .tesseract_engine import TesseractOcrEngine

_REGISTRY: Dict[str, type] = {
    "tesseract": TesseractOcrEngine,
}

_INSTANCES: Dict[str, OcrEngine] = {}


def _noop(message: str) -> None:
    return None


def get_ocr_engine(name: str = "tesseract", log: Callable[[str], None] = _noop) -> OcrEngine:
    """Return a cached OCR engine instance for ``name``.

    Unknown names fall back to tesseract with a warning logged instead of
    raising, so a bad config value degrades gracefully rather than crashing
    a batch run.
    """
    key = (name or "tesseract").lower()
    if key not in _REGISTRY:
        log(f"      -> [OCR] 알 수 없는 OCR 엔진 '{name}' -> tesseract로 대체합니다.")
        key = "tesseract"

    if key not in _INSTANCES:
        _INSTANCES[key] = _REGISTRY[key]()

    return _INSTANCES[key]


def available_engines() -> list:
    """Names of all registered OCR engines."""
    return list(_REGISTRY.keys())
