"""Batch conversion pipeline, free of any GUI dependency.

The GUI passes ``log``/``progress`` callables that emit Qt signals; the CLI
passes ``print``. Nothing in here imports Qt, so the same code path serves both.

Google Drive and Notion are each optional. With Drive off, converted Markdown
stays in the output directory instead of being uploaded and deleted, and Notion
pages are created without a URL property.
"""

import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Sequence

from . import config as config_mod
from . import dispatch, drive, history, notion_sync
from .ocr.base import OcrCancelled
from .ocr.factory import get_ocr_engine


def _noop(*_args, **_kwargs) -> None:
    pass


@dataclass
class FileOutcome:
    name: str
    status: str  # "converted" | "duplicate" | "skipped" | "failed" | "cancelled"
    engine: str = ""
    ocr_pages: int = 0
    message: str = ""


@dataclass
class ProcessSummary:
    total: int = 0
    converted: int = 0
    duplicates: int = 0
    skipped: int = 0
    failed: int = 0
    cancelled: bool = False
    outcomes: List[FileOutcome] = field(default_factory=list)


def _unique_destination(directory: Path, name: str, stem: str, suffix: str) -> Path:
    """Avoid clobbering a file of the same name already in the archive."""
    dest = directory / name
    if dest.exists():
        dest = directory / f"{stem}_{int(time.time())}{suffix}"
    return dest


def _unique_output_path(output_dir: Path, stem: str, source_suffix: str) -> Path:
    """Pick an output Markdown path, avoiding collisions between same-stem sources.

    ``report.pdf`` and ``report.docx`` in the same batch would otherwise both
    target ``report.md``, silently clobbering one another. Keeps the plain
    ``<stem>.md`` when free (the common case keeps clean names) and
    disambiguates on collision using the source extension, falling back to a
    numeric suffix if even that is already taken.
    """
    candidate = output_dir / f"{stem}.md"
    if not candidate.exists():
        return candidate

    ext_tag = source_suffix.lstrip(".").lower()
    if ext_tag:
        candidate = output_dir / f"{stem}_{ext_tag}.md"
        if not candidate.exists():
            return candidate

    n = 2
    while True:
        name = f"{stem}_{ext_tag}_{n}.md" if ext_tag else f"{stem}_{n}.md"
        candidate = output_dir / name
        if not candidate.exists():
            return candidate
        n += 1


def process_files(
    cfg,
    files: Optional[Sequence[Path]] = None,
    log: Callable[[str], None] = _noop,
    progress: Callable[[int, int], None] = _noop,
    should_cancel: Optional[Callable[[], bool]] = None,
    archive_originals: Optional[bool] = None,
) -> ProcessSummary:
    """Convert files, then optionally upload to Drive and sync to Notion.

    ``files`` defaults to every file directly inside the configured input
    directory. Cancellation is checked between files only -- aborting mid-upload
    would leave history.json disagreeing with what is actually on Drive.

    ``archive_originals`` controls whether source files are moved into the
    processed directory once handled. It defaults to "only in input-folder
    scan mode": dropping a file into ``input_files/`` is a hand-off, so moving
    it out is expected, but a file the user picked from somewhere else on disk
    still belongs to that folder and must be left exactly where it was.
    """
    summary = ProcessSummary()
    cancelled = should_cancel or (lambda: False)
    scan_mode = files is None
    if archive_originals is None:
        archive_originals = scan_mode

    input_dir = cfg.resolved_input_dir()
    processed_dir = cfg.resolved_processed_dir()
    output_dir = cfg.resolved_output_dir()
    for d in (input_dir, processed_dir, output_dir):
        d.mkdir(parents=True, exist_ok=True)

    # --- Google Drive (optional) ---------------------------------------
    service = None
    folder_id = None
    if cfg.enable_drive:
        log("[1/3] Google Drive 인증 중...")
        service = drive.get_google_drive_service(log=log)
        if not service:
            log("[중단] Google Drive 인증에 실패했습니다. 설정에서 연동을 끄거나 credentials.json을 등록하세요.")
            return summary
        folder_id = drive.get_or_create_folder(service, cfg.drive_folder_name, log=log)
        if not folder_id:
            log("[중단] Google Drive 대상 폴더를 찾거나 만들지 못했습니다.")
            return summary
    else:
        log("[1/3] Google Drive 연동이 꺼져 있습니다. 변환 결과를 로컬에 저장합니다.")

    # --- Notion (optional) ---------------------------------------------
    notion = notion_sync.build_notion_client(cfg, log=log)
    if notion:
        log("[2/3] Notion 연동이 활성화되었습니다.")
    elif cfg.enable_notion:
        log("[2/3] Notion 설정이 비어 있어 동기화를 건너뜁니다.")
    else:
        log("[2/3] Notion 연동이 꺼져 있습니다.")

    # --- Collect work ---------------------------------------------------
    if files is None:
        files = [f for f in input_dir.iterdir() if f.is_file()]
    files = list(files)
    summary.total = len(files)

    if not files:
        log(f"[정보] '{input_dir.name}'에 변환할 파일이 없습니다.")
        return summary

    log(f"[3/3] {len(files)}개 파일을 처리합니다.")
    if archive_originals:
        log(f"      완료된 원본은 '{processed_dir.name}' 폴더로 이동합니다.")
    else:
        log("      직접 선택한 파일이므로 원본은 원래 위치에 그대로 둡니다.")
    ocr_engine = get_ocr_engine("tesseract", log=log)
    history_data = history.load_history()

    for index, file_path in enumerate(files):
        if cancelled():
            summary.cancelled = True
            log("[취소] 사용자가 작업을 중단했습니다.")
            break

        progress(index, len(files))

        if not dispatch.is_supported(file_path):
            log(f"  → [건너뜀] 지원하지 않는 확장자: {file_path.name}")
            summary.skipped += 1
            summary.outcomes.append(FileOutcome(file_path.name, "skipped", message="지원하지 않는 확장자"))
            continue

        log(f"\n▶ {file_path.name}")

        file_hash = history.get_file_hash(file_path)
        if file_hash in history_data:
            if archive_originals:
                log("  → [중복] 이미 변환된 파일입니다. 보관 폴더로 이동합니다.")
                dest = _unique_destination(processed_dir, file_path.name, file_path.stem, file_path.suffix)
                shutil.move(str(file_path), str(dest))
            else:
                log("  → [중복] 이미 변환된 파일입니다. 건너뜁니다.")
            summary.duplicates += 1
            summary.outcomes.append(FileOutcome(file_path.name, "duplicate"))
            continue

        try:
            result = dispatch.convert_file(
                file_path,
                ocr_lang=cfg.ocr_lang,
                ocr_engine=ocr_engine,
                log=lambda m: log(f"  {m}"),
                should_cancel=cancelled,
            )

            # A long OCR run may have been cancelled mid-flight; dispatch still
            # returns whatever pages it finished. Re-check before any
            # irreversible step so a cancelled run is never committed as a
            # successful conversion.
            if cancelled():
                summary.cancelled = True
                log("  → [취소] 변환 중 작업이 취소되었습니다. 이 파일은 기록하지 않습니다.")
                summary.outcomes.append(
                    FileOutcome(file_path.name, "cancelled", result.engine, result.ocr_pages, "사용자 취소")
                )
                break

            md_content = result.markdown or ""
            md_file_path = _unique_output_path(output_dir, file_path.stem, file_path.suffix)

            with open(md_file_path, "w", encoding="utf-8") as f:
                f.write(md_content)

            drive_link = None
            drive_file_id = None
            upload_ok = True

            if service and folder_id:
                log(f"  - Google Drive '{cfg.drive_folder_name}' 폴더에 업로드 중...")
                upload_ok, drive_link, drive_file_id = drive.upload_file(
                    service, str(md_file_path), folder_id, log=log
                )

            if not upload_ok:
                # Leave both the .md and the source in place so a retry is possible.
                log("  - [실패] 업로드에 실패했습니다. 원본과 변환 결과를 그대로 둡니다.")
                summary.failed += 1
                summary.outcomes.append(
                    FileOutcome(file_path.name, "failed", result.engine, result.ocr_pages, "Drive 업로드 실패")
                )
                continue

            notion_page_id = None
            if notion:
                log("  - Notion에 동기화 중...")
                ok, page_id = notion_sync.sync_to_notion(
                    notion, cfg.notion_database_id, md_file_path.name, drive_link, md_content, log=log
                )
                if ok:
                    notion_page_id = page_id

            # Record history *before* the irreversible steps below (deleting
            # the local Markdown, moving the source out of the input dir) so
            # that if one of those fails, the run is left in a recoverable
            # state -- recorded as done, ready to be archived cleanly next
            # run -- rather than lost with no record and a moved source.
            record = {
                "original_name": file_path.name,
                "drive_link": drive_link,
                "drive_file_id": drive_file_id,
                "notion_page_id": notion_page_id,
            }
            history.update_history(file_hash, record)
            history_data[file_hash] = record  # keep local view in sync for later duplicates this run

            # The converted Markdown always stays in the output directory. The
            # original script deleted it, but there it was a scratch file in
            # app/ with no output folder to speak of -- Drive was the only
            # result. Now that there is a real output_files/, throwing its
            # contents away whenever Drive happens to be on is just surprising.
            log(f"  - 저장: {md_file_path}")

            if archive_originals:
                dest = _unique_destination(processed_dir, file_path.name, file_path.stem, file_path.suffix)
                shutil.move(str(file_path), str(dest))

            engine_note = f"{result.engine}"
            if result.ocr_pages:
                engine_note += f", OCR {result.ocr_pages}페이지"
            log(f"  - [완료] ({engine_note})")
            summary.converted += 1
            summary.outcomes.append(
                FileOutcome(file_path.name, "converted", result.engine, result.ocr_pages)
            )

        except OcrCancelled:
            # A cancelled OCR run is not a failure -- nothing has been written,
            # uploaded or moved yet, so the file stays put for the next run.
            summary.cancelled = True
            log("  → [취소] OCR 도중 작업이 취소되었습니다. 이 파일은 기록하지 않습니다.")
            summary.outcomes.append(FileOutcome(file_path.name, "cancelled", message="사용자 취소"))
            break

        except Exception as e:
            log(f"  - [오류] '{file_path.name}' 처리 실패: {e}")
            summary.failed += 1
            summary.outcomes.append(FileOutcome(file_path.name, "failed", message=str(e)))

    progress(len(files), len(files))
    log(
        f"\n[결과] 변환 {summary.converted} / 중복 {summary.duplicates} / "
        f"건너뜀 {summary.skipped} / 실패 {summary.failed}"
    )
    return summary
