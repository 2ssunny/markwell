"""Path resolution that works both from source and from a PyInstaller bundle.

Two distinct kinds of path are needed:

* **Resources** -- read-only files shipped with the app (portable tesseract,
  the seed tessdata models, the icon). Frozen builds extract these next to
  ``sys._MEIPASS``; from source they live in ``<repo>/resources``.
* **User data** -- files the app writes (config.json, token.json, history.json,
  the writable tessdata copy). Frozen builds must not write inside the bundle,
  so these go to ``%LOCALAPPDATA%\\Markwell``. From source they stay in
  ``<repo>/app`` so an existing checkout keeps working untouched.
"""

import os
import shutil
import sys
from pathlib import Path

from . import APP_NAME, LEGACY_APP_NAMES


def is_frozen() -> bool:
    """True when running from a PyInstaller bundle."""
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")


def bundle_dir() -> Path:
    """Root the app was loaded from: the extraction dir when frozen, else the repo."""
    if is_frozen():
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parents[1]


def resource_path(*parts: str) -> Path:
    """Absolute path to a bundled read-only resource under ``resources/``."""
    return bundle_dir().joinpath("resources", *parts)


_STATE_FILES = ("config.json", "credentials.json", "token.json", "history.json")


def _local_app_data() -> Path:
    local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(local)


def _migrate_from_legacy(base: Path) -> None:
    """Carry settings over from a previous app name, once.

    Renaming the app moves its data directory, which otherwise looks exactly
    like a fresh install with every setting gone -- Drive would even ask to
    re-authenticate. Only ever copies into an empty destination and never
    overwrites, so this is a no-op on every run after the first.
    """
    if any((base / name).exists() for name in _STATE_FILES):
        return

    for legacy_name in LEGACY_APP_NAMES:
        legacy = _local_app_data() / legacy_name
        if not legacy.is_dir() or legacy.resolve() == base.resolve():
            continue
        copied = False
        for name in _STATE_FILES:
            source = legacy / name
            if source.is_file():
                shutil.copy2(source, base / name)
                copied = True
        if copied:
            return


def user_data_dir() -> Path:
    """Writable directory for config, credentials, token and history."""
    if is_frozen():
        base = _local_app_data() / APP_NAME
        base.mkdir(parents=True, exist_ok=True)
        _migrate_from_legacy(base)
    else:
        base = Path(__file__).resolve().parents[1] / "app"
        base.mkdir(parents=True, exist_ok=True)
    return base


def default_workspace_dir() -> Path:
    """Root holding input_files/, processed_files/ and output_files/.

    From source this is the repo root, matching the current layout. When frozen
    the bundle is not a sensible workspace, so it falls back to the user data
    directory; the GUI lets the user override all three folders anyway.
    """
    if is_frozen():
        return user_data_dir()
    return Path(__file__).resolve().parents[1]
