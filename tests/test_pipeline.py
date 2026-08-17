"""markwell/converter_service.py (batch pipeline) and markwell/history.py.

All fixture files here are genuinely text-based PDFs/plain text/CSV/XLSX, so
process_files()'s internal `get_ocr_engine("tesseract", ...)` call never
actually reaches an OCR pass -- these tests exercise the pipeline's own
archiving/dedup/collision/cancellation logic, not OCR.
"""

import json
import threading

import pytest

from markwell import config as config_mod
from markwell import converter_service, drive, history

from .conftest import make_cfg, write_csv, write_text_pdf, write_txt


# ---------------------------------------------------------------------------
# Queue mode vs. scan mode: does an original get archived?
# ---------------------------------------------------------------------------


def test_queue_mode_never_moves_originals(tmp_path):
    cfg = make_cfg(tmp_path)
    sources = tmp_path / "sources"
    sources.mkdir()
    picked = write_text_pdf(sources / "picked.pdf", "Queue mode source content. " * 5)

    summary = converter_service.process_files(cfg, files=[picked])

    assert summary.converted == 1
    assert picked.exists(), "a file explicitly picked by the user must stay exactly where it was"
    assert not (cfg.resolved_processed_dir() / "picked.pdf").exists()


def test_queue_mode_never_moves_duplicate_originals_either(tmp_path):
    import shutil

    cfg = make_cfg(tmp_path)
    sources = tmp_path / "sources"
    sources.mkdir()
    first = write_text_pdf(sources / "first.pdf", "Duplicate-across-queue-runs content. " * 5)
    converter_service.process_files(cfg, files=[first])

    # byte-for-byte copy, not a second fitz.save() -- PyMuPDF embeds a fresh
    # document ID/timestamp on every save, so two independently generated PDFs
    # with identical visible text are NOT identical bytes (and thus not a hash
    # dedup match). A real duplicate is the same bytes under a new name.
    second = sources / "second.pdf"
    shutil.copyfile(first, second)
    summary = converter_service.process_files(cfg, files=[second])

    assert summary.duplicates == 1
    assert second.exists(), "a duplicate found in queue mode must still not be moved"
    assert not (cfg.resolved_processed_dir() / "second.pdf").exists()


def test_scan_mode_archives_converted_originals(tmp_path):
    cfg = make_cfg(tmp_path)
    input_dir = cfg.resolved_input_dir()
    input_dir.mkdir(parents=True)
    write_text_pdf(input_dir / "scanned_in.pdf", "Scan mode source content. " * 5)

    summary = converter_service.process_files(cfg)

    assert summary.converted == 1
    assert not (input_dir / "scanned_in.pdf").exists(), "scan mode must drain input_files/"
    assert (cfg.resolved_processed_dir() / "scanned_in.pdf").exists(), "...into processed_files/"


# ---------------------------------------------------------------------------
# Converted markdown is always kept in the output dir
# ---------------------------------------------------------------------------


def test_converted_markdown_kept_with_drive_off(tmp_path):
    cfg = make_cfg(tmp_path, enable_drive=False)
    sources = tmp_path / "sources"
    sources.mkdir()
    src = write_txt(sources / "note.txt", "Drive-off content")

    summary = converter_service.process_files(cfg, files=[src])

    assert summary.converted == 1
    md_files = list(cfg.resolved_output_dir().glob("*.md"))
    assert len(md_files) == 1
    assert "Drive-off content" in md_files[0].read_text(encoding="utf-8")

    hist = history.load_history()
    assert len(hist) == 1
    record = next(iter(hist.values()))
    assert record["drive_link"] is None


def test_converted_markdown_kept_with_drive_on_stubbed(tmp_path, monkeypatch):
    cfg = make_cfg(tmp_path, enable_drive=True)
    sources = tmp_path / "sources"
    sources.mkdir()
    src = write_txt(sources / "note.txt", "Drive-on content")

    monkeypatch.setattr(drive, "get_google_drive_service", lambda log=lambda m: None: object())
    monkeypatch.setattr(drive, "get_or_create_folder", lambda service, name, log=lambda m: None: "fake-folder-id")
    uploaded = {}

    def fake_upload_file(service, file_path, folder_id, log=lambda m: None):
        uploaded["path"] = file_path
        return True, "https://drive.example/fake-link", "fake-file-id"

    monkeypatch.setattr(drive, "upload_file", fake_upload_file)

    summary = converter_service.process_files(cfg, files=[src])

    assert summary.converted == 1
    md_files = list(cfg.resolved_output_dir().glob("*.md"))
    assert len(md_files) == 1, "the local .md must be kept even though Drive is on"
    assert "Drive-on content" in md_files[0].read_text(encoding="utf-8")
    assert uploaded["path"] == str(md_files[0])

    hist = history.load_history()
    record = next(iter(hist.values()))
    assert record["drive_link"] == "https://drive.example/fake-link"
    assert record["drive_file_id"] == "fake-file-id"


# ---------------------------------------------------------------------------
# Output filename collisions
# ---------------------------------------------------------------------------


def test_output_filenames_do_not_collide_for_same_stem(tmp_path):
    cfg = make_cfg(tmp_path)
    sources = tmp_path / "sources"
    sources.mkdir()
    pdf_src = write_text_pdf(sources / "report.pdf", "PDF SOURCE CONTENT MARKER. " * 5)
    txt_src = write_txt(sources / "report.txt", "TXT SOURCE CONTENT MARKER")

    summary = converter_service.process_files(cfg, files=[pdf_src, txt_src])

    assert summary.converted == 2
    md_files = sorted(p.name for p in cfg.resolved_output_dir().glob("*.md"))
    assert len(md_files) == 2, f"expected 2 distinct .md outputs, got {md_files}"

    contents = {p.name: p.read_text(encoding="utf-8") for p in cfg.resolved_output_dir().glob("*.md")}
    pdf_output = [text for name, text in contents.items() if "PDF SOURCE CONTENT MARKER" in text]
    txt_output = [text for name, text in contents.items() if "TXT SOURCE CONTENT MARKER" in text]
    assert len(pdf_output) == 1
    assert len(txt_output) == 1


# ---------------------------------------------------------------------------
# SHA-256 dedup, across both modes
# ---------------------------------------------------------------------------


def test_sha256_dedup_in_queue_mode(tmp_path):
    cfg = make_cfg(tmp_path)
    sources = tmp_path / "sources"
    sources.mkdir()
    content = "Same bytes, different filename. " * 4
    a = write_txt(sources / "a.txt", content)
    converter_service.process_files(cfg, files=[a])

    b = write_txt(sources / "b.txt", content)
    summary = converter_service.process_files(cfg, files=[b])

    assert summary.duplicates == 1
    assert summary.converted == 0


def test_sha256_dedup_in_scan_mode(tmp_path):
    cfg = make_cfg(tmp_path)
    input_dir = cfg.resolved_input_dir()
    input_dir.mkdir(parents=True)
    content = "Same bytes, different filename, scan mode. " * 4
    (input_dir / "c.txt").write_text(content, encoding="utf-8")
    converter_service.process_files(cfg)

    input_dir2 = cfg.resolved_input_dir()
    (input_dir2 / "d.txt").write_text(content, encoding="utf-8")
    summary = converter_service.process_files(cfg)

    assert summary.duplicates == 1
    # duplicates ARE archived in scan mode (archive_originals defaults True there)
    assert (cfg.resolved_processed_dir() / "d.txt").exists()
    assert not (input_dir2 / "d.txt").exists()


# ---------------------------------------------------------------------------
# Unsupported extensions
# ---------------------------------------------------------------------------


def test_unsupported_extension_is_skipped_and_left_in_place(tmp_path):
    cfg = make_cfg(tmp_path)
    input_dir = cfg.resolved_input_dir()
    input_dir.mkdir(parents=True)
    mystery = input_dir / "mystery.xyz"
    mystery.write_text("no engine handles this", encoding="utf-8")
    write_text_pdf(input_dir / "valid.pdf", "A valid convertible document. " * 5)

    summary = converter_service.process_files(cfg)

    assert summary.skipped == 1
    assert summary.converted == 1
    assert mystery.exists(), "an unsupported file must never be archived, even in scan mode"
    assert not (cfg.resolved_processed_dir() / "mystery.xyz").exists()
    assert (cfg.resolved_processed_dir() / "valid.pdf").exists()


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------


def test_cancellation_mid_batch_leaves_remaining_file_unarchived_and_unrecorded(tmp_path):
    cfg = make_cfg(tmp_path)
    input_dir = cfg.resolved_input_dir()
    input_dir.mkdir(parents=True)
    write_text_pdf(input_dir / "one.pdf", "First document content. " * 5)
    write_text_pdf(input_dir / "two.pdf", "Second document content. " * 5)

    # process_files checks should_cancel() twice per successfully-converted,
    # non-OCR file: once at the top of the loop and once again right after
    # dispatch.convert_file() returns (to catch cancellation during a long OCR
    # run). Let both checks for the first file through, then cancel on the
    # second file's loop-top check.
    calls = {"n": 0}

    def should_cancel():
        calls["n"] += 1
        return calls["n"] > 2

    summary = converter_service.process_files(cfg, should_cancel=should_cancel)

    assert summary.cancelled is True
    assert summary.converted == 1
    remaining_in_input = list(input_dir.glob("*.pdf"))
    archived = list(cfg.resolved_processed_dir().glob("*.pdf"))
    assert len(remaining_in_input) == 1, "the un-processed file must stay in input_files/"
    assert len(archived) == 1, "only the processed file may be archived"
    assert len(history.load_history()) == 1, "the cancelled file must not be recorded"


# ---------------------------------------------------------------------------
# history.py: atomic, concurrency-safe read-modify-write
# ---------------------------------------------------------------------------


def test_history_update_survives_many_concurrent_writers(tmp_path):
    n_threads = 8
    n_per_thread = 25

    def worker(t):
        for i in range(n_per_thread):
            h = f"hash-{t}-{i}"
            history.update_history(h, {"original_name": h, "drive_link": None, "drive_file_id": None, "notion_page_id": None})

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    data = history.load_history()
    assert len(data) == n_threads * n_per_thread

    raw = json.loads(config_mod.history_path().read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    assert len(raw) == n_threads * n_per_thread


def test_history_remove_concurrent_with_updates_loses_nothing(tmp_path):
    for i in range(20):
        history.update_history(f"seed-{i}", {"original_name": f"seed-{i}"})

    def updater(t):
        for i in range(20):
            history.update_history(f"new-{t}-{i}", {"original_name": f"new-{t}-{i}"})

    def remover():
        # remove the even-numbered seed entries while updaters are still running
        history.remove_history_entries([f"seed-{i}" for i in range(0, 20, 2)])

    threads = [threading.Thread(target=updater, args=(t,)) for t in range(4)]
    threads.append(threading.Thread(target=remover))
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    data = history.load_history()
    assert len(data) == 10 + 4 * 20  # 10 odd seed entries survive + all new entries
    for i in range(0, 20, 2):
        assert f"seed-{i}" not in data
    for i in range(1, 20, 2):
        assert f"seed-{i}" in data

    # the file must always be valid JSON after concurrent access
    raw = json.loads(config_mod.history_path().read_text(encoding="utf-8"))
    assert isinstance(raw, dict)


@pytest.mark.parametrize("raw_content", ["[]", '"just a string"', "42", "null"])
def test_load_history_returns_empty_dict_for_non_dict_json(tmp_path, raw_content):
    path = config_mod.history_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw_content, encoding="utf-8")

    assert history.load_history() == {}
