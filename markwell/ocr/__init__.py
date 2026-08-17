"""OCR backends for scanned/mixed PDF pages."""

from .base import OcrEngine
from .factory import available_engines, get_ocr_engine
from .tesseract_engine import TesseractOcrEngine

__all__ = [
    "OcrEngine",
    "TesseractOcrEngine",
    "get_ocr_engine",
    "available_engines",
]
