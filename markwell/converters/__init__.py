"""Conversion engines that turn a document file into Markdown.

``ConversionResult`` is the shared return type for every engine in this
package, so ``dispatch.py`` can treat pdf/anydoc/markitdown engines
uniformly regardless of which one actually produced the text.
"""

from dataclasses import dataclass, field
from typing import List


@dataclass
class ConversionResult:
    """Result of converting a single file to Markdown.

    Attributes:
        markdown: The converted Markdown text.
        engine: Name of the engine that actually produced the text
            (e.g. ``"pdf-inspector"``, ``"tesseract-ocr"``, ``"markitdown"``,
            ``"anydoc"``).
        ocr_pages: Number of pages that went through OCR (0 if none).
        notes: Human-readable notes/warnings collected during conversion.
    """

    markdown: str
    engine: str
    ocr_pages: int = 0
    notes: List[str] = field(default_factory=list)


__all__ = ["ConversionResult"]
