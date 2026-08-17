"""markwell — document to Markdown batch converter with Drive/Notion sync."""

import sys

__version__ = "2.0.0"
APP_NAME = "Markwell"

# The app used to store its data under this name. Kept so a user upgrading from
# an older build keeps their config, credentials, token and history instead of
# being met with an apparently blank install.
LEGACY_APP_NAMES = ("MarkItDownBatch",)


def configure_console_encoding() -> None:
    """Make stdout/stderr survive non-ASCII output.

    Log messages are Korean, and a Windows console defaults to a legacy code
    page (cp949 here), so a bare print() raises UnicodeEncodeError and takes
    down the whole run. Frozen console builds hit this immediately. Windowed
    builds have no real stdout at all, hence the None guard.
    """
    for stream in (sys.stdout, sys.stderr):
        if stream is None:
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            # Already-detached or non-reconfigurable stream; nothing to do.
            pass
