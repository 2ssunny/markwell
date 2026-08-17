"""Conversion history: SHA-256 file hashing and history.json load/save.

Record schema (unchanged from the legacy app): each entry maps a file hash to
``{original_name, drive_link, drive_file_id, notion_page_id}``.

Reads and writes are guarded by a module-level lock and writes are atomic
(temp file + os.replace), because the GUI can run a conversion and a sync
check concurrently against the same history.json.
"""

import hashlib
import json
import os
import tempfile
import threading

from . import config

# Serializes all read-modify-write access to history.json across threads.
_lock = threading.Lock()


def get_file_hash(filepath) -> str:
    """Calculate SHA-256 hash of a file's contents."""
    hasher = hashlib.sha256()
    with open(filepath, "rb") as f:
        while True:
            chunk = f.read(8192)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def _read_history_locked() -> dict:
    """Read history.json. Caller must hold ``_lock``."""
    path = config.history_path()
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return {}
    if not isinstance(data, dict):
        # A syntactically valid but structurally wrong root (e.g. "[]" or a
        # bare string) must not propagate -- callers index/iterate as a dict.
        return {}
    return data


def _write_history_locked(history_data: dict) -> None:
    """Atomically write history.json. Caller must hold ``_lock``."""
    path = config.history_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), prefix=".history_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(history_data, f, indent=4, ensure_ascii=False)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def load_history() -> dict:
    with _lock:
        return _read_history_locked()


def save_history(history_data: dict) -> None:
    with _lock:
        _write_history_locked(history_data)


def update_history(file_hash: str, record: dict) -> None:
    """Merge a single entry into history.json under the lock.

    Re-reads the current file before writing so a conversion never clobbers
    changes made concurrently by another worker (e.g. the sync checker).
    """
    with _lock:
        data = _read_history_locked()
        data[file_hash] = record
        _write_history_locked(data)


def remove_history_entries(hashes) -> None:
    """Remove the given hashes from history.json under the lock, if present."""
    hashes = set(hashes)
    if not hashes:
        return
    with _lock:
        data = _read_history_locked()
        for h in hashes:
            data.pop(h, None)
        _write_history_locked(data)
