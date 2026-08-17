# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for MarkItDown Batch.

Built as --onedir rather than --onefile on purpose: the bundle carries ~110MB
of portable Tesseract and language models, and onefile would re-extract all of
it to %TEMP% on every launch. onedir still produces a single clickable exe,
it just keeps its dependencies in the folder beside it.

Build:  pyinstaller packaging/markwell.spec --noconfirm
"""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

REPO = Path(SPECPATH).parent

# Set to True to produce a debug build with a console window. Windowed builds
# swallow import-time tracebacks, so always debug with this on first.
CONSOLE = False

datas = []
binaries = []
hiddenimports = []

# Conda builds _ssl.pyd against the OpenSSL in <env>\Library\bin, which is not a
# location PyInstaller scans. Without these, PyInstaller picks up an older
# OpenSSL from somewhere else on the path and the frozen app dies at startup
# with "DLL load failed while importing _ssl: The specified procedure could not
# be found" -- which takes out Google Drive, Notion and anything else using TLS.
# Listing them explicitly puts the matching pair in the bundle root, where they
# win over any other copy.
conda_library_bin = Path(sys.prefix) / "Library" / "bin"
if conda_library_bin.is_dir():
    for dll in ("libssl-3-x64.dll", "libcrypto-3-x64.dll"):
        candidate = conda_library_bin / dll
        if candidate.exists():
            binaries.append((str(candidate), "."))

# The portable OCR runtime and the seed language models. Tesseract resolves its
# DLLs from its own directory, so the whole folder has to travel together.
tesseract_dir = REPO / "resources" / "tesseract"
if tesseract_dir.exists():
    for f in tesseract_dir.iterdir():
        if f.is_file():
            datas.append((str(f), "resources/tesseract"))

tessdata_dir = REPO / "resources" / "tessdata_best"
if tessdata_dir.exists():
    for f in tessdata_dir.glob("*.traineddata"):
        datas.append((str(f), "resources/tessdata_best"))

icon_file = REPO / "resources" / "icon.ico"
if icon_file.exists():
    datas.append((str(icon_file), "resources"))

# markitdown discovers its converters by import, and magika ships ONNX model
# files as package data that a plain module scan misses entirely.
for pkg in ("markitdown", "magika", "pdf_inspector", "anydoc"):
    try:
        pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg)
        datas += pkg_datas
        binaries += pkg_binaries
        hiddenimports += pkg_hidden
    except Exception:
        # A missing optional package must not break the build; the app degrades
        # with a clear error at runtime instead.
        pass

hiddenimports += collect_submodules("markitdown.converters")
hiddenimports += [
    "fitz",
    "pytesseract",
    "PIL.Image",
    "notion_client",
    "googleapiclient.discovery",
    "google_auth_oauthlib.flow",
    "google.auth.transport.requests",
    "google.oauth2.credentials",
]

a = Analysis(
    [str(REPO / "main.py")],
    pathex=[str(REPO)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # Pulled in transitively by markitdown's optional converters but never used
    # by this app; excluding them keeps the bundle from ballooning. markitdown
    # guards each optional converter behind a dependency check, so a missing one
    # reports itself unavailable instead of breaking the import.
    # speech_recognition alone is ~42MB of audio transcription this app never calls.
    excludes=[
        "tkinter", "torch", "torchvision", "easyocr", "matplotlib", "pytest",
        "speech_recognition", "pydub", "youtube_transcript_api",
    ],
    noarchive=False,
)

# google-api-python-client ships a cached discovery document for every Google
# API in existence -- ~98MB, of which this app uses exactly one. Keeping only
# drive.* leaves the offline cache working for the API we call; anything else
# would fall back to a network fetch, which we never trigger.
def _keep_datum(entry):
    dest = entry[0].replace("\\", "/")
    if "googleapiclient/discovery_cache/documents/" not in dest:
        return True
    return dest.rsplit("/", 1)[-1].startswith("drive.")


a.datas = [d for d in a.datas if _keep_datum(d)]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Markwell",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=CONSOLE,
    icon=str(icon_file) if icon_file.exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Markwell",
)
