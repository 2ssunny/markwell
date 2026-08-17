"""Thin wrapper around the ``markitdown`` library.

Used both as a primary engine (for extensions markitdown handles natively)
and as a fallback engine when pdf-inspector or anydoc fail.
"""

from pathlib import Path
from typing import Callable, Optional, Union

_MD_INSTANCE = None  # lazily-created module-level MarkItDown() instance


def _noop(message: str) -> None:
    return None


def _get_markitdown():
    global _MD_INSTANCE
    if _MD_INSTANCE is None:
        from markitdown import MarkItDown

        # Construct with no arguments: enable_plugins=True triggers an
        # importlib.metadata entry-point scan that breaks under PyInstaller.
        _MD_INSTANCE = MarkItDown()
    return _MD_INSTANCE


def convert(path: Union[str, Path], log: Callable[[str], None] = _noop) -> str:
    """Convert ``path`` to Markdown using markitdown.

    Args:
        path: File to convert.
        log: Optional progress/log callback.

    Returns:
        The converted Markdown text (may be empty).
    """
    md = _get_markitdown()
    log(f"      -> [markitdown] '{Path(path).name}' 변환 중...")
    result = md.convert(str(path))
    return result.text_content or ""
