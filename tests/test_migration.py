"""markwell/paths.py: legacy-install migration (MarkItDownBatch -> Markwell).

These tests exercise the REAL paths.user_data_dir() (they carry
@pytest.mark.real_paths to opt out of conftest's autouse stub), driving it
through a faked LOCALAPPDATA (set to tmp_path) and paths.is_frozen() forced
True -- exactly as the task spec prescribes -- so nothing here ever touches
the real %LOCALAPPDATA% or <repo>/app.
"""

import pytest

from markwell import APP_NAME, LEGACY_APP_NAMES
from markwell import paths as paths_mod

pytestmark = pytest.mark.real_paths

STATE_FILES = ("config.json", "credentials.json", "token.json", "history.json")


def _use_fake_localappdata(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(paths_mod, "is_frozen", lambda: True)


def _write_legacy_state(tmp_path):
    legacy_dir = tmp_path / LEGACY_APP_NAMES[0]
    legacy_dir.mkdir()
    for name in STATE_FILES:
        (legacy_dir / name).write_text(f"legacy-{name}", encoding="utf-8")
    return legacy_dir


def test_legacy_state_files_are_copied_on_first_call(tmp_path, monkeypatch):
    _use_fake_localappdata(monkeypatch, tmp_path)
    _write_legacy_state(tmp_path)

    base = paths_mod.user_data_dir()

    assert base == tmp_path / APP_NAME
    for name in STATE_FILES:
        assert (base / name).read_text(encoding="utf-8") == f"legacy-{name}"


def test_legacy_folder_is_left_intact_not_moved(tmp_path, monkeypatch):
    _use_fake_localappdata(monkeypatch, tmp_path)
    legacy_dir = _write_legacy_state(tmp_path)

    paths_mod.user_data_dir()

    for name in STATE_FILES:
        assert (legacy_dir / name).exists(), "migration must copy, never move, the legacy files"
        assert (legacy_dir / name).read_text(encoding="utf-8") == f"legacy-{name}"


def test_second_call_does_not_clobber_edits_made_after_migration(tmp_path, monkeypatch):
    _use_fake_localappdata(monkeypatch, tmp_path)
    _write_legacy_state(tmp_path)

    base = paths_mod.user_data_dir()
    (base / "config.json").write_text("edited-after-migration", encoding="utf-8")

    base_again = paths_mod.user_data_dir()

    assert base_again == base
    assert (base_again / "config.json").read_text(encoding="utf-8") == "edited-after-migration"
    # the other, untouched state files must also still be exactly what migration copied
    assert (base_again / "history.json").read_text(encoding="utf-8") == "legacy-history.json"


def test_clean_install_with_no_legacy_folder_creates_nothing(tmp_path, monkeypatch):
    _use_fake_localappdata(monkeypatch, tmp_path)
    assert not (tmp_path / LEGACY_APP_NAMES[0]).exists()

    base = paths_mod.user_data_dir()

    assert base == tmp_path / APP_NAME
    assert base.exists()
    assert list(base.iterdir()) == [], "no legacy folder means no state files should appear"


def test_migration_ignores_a_legacy_folder_with_no_recognized_state_files(tmp_path, monkeypatch):
    _use_fake_localappdata(monkeypatch, tmp_path)
    legacy_dir = tmp_path / LEGACY_APP_NAMES[0]
    legacy_dir.mkdir()
    (legacy_dir / "unrelated.txt").write_text("not a state file", encoding="utf-8")

    base = paths_mod.user_data_dir()

    assert list(base.iterdir()) == []
