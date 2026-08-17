"""Thin wrapper around ``firecrawl-anydoc`` for office-document formats.

Exceptions propagate to the caller (``dispatch.py``) uncaught -- dispatch
decides whether/how to fall back. Besides ``anydoc.ConvertError`` (and
subclasses), the installed anydoc's own documentation notes ``to_markdown``
can also raise ``OSError`` when the file can't be read, and the lazy
``import anydoc`` below can itself raise ``ImportError`` if the module
failed to bundle in a packaged build. dispatch.py's ``_convert_anydoc``
catches broadly for exactly this reason.
"""

from pathlib import Path
from typing import Callable, Union

def _noop(message: str) -> None:
    return None


def convert(path: Union[str, Path], log: Callable[[str], None] = _noop) -> str:
    """Convert ``path`` to Markdown using anydoc.

    Args:
        path: File to convert.
        log: Optional progress/log callback.

    Returns:
        The converted Markdown text.

    Raises:
        anydoc.ConvertError: (or a subclass) on any conversion failure.
        OSError: if anydoc can't read the file (per anydoc's own docs).
        ImportError: if the ``anydoc`` module itself failed to import.
        All are left to propagate -- the caller decides the fallback.
    """
    import anydoc

    log(f"      -> [anydoc] '{Path(path).name}' 변환 중...")
    return anydoc.to_markdown(str(path))


def format_supported(ext: str) -> bool:
    """Whether anydoc recognizes ``ext`` (with or without leading dot)."""
    import anydoc

    return anydoc.format_from_extension(ext) is not None
