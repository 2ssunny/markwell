"""Reconcile local history with Google Drive.

When a converted Markdown file is deleted from Drive, the matching Notion page
is archived and the history entry dropped, so re-adding the source file
converts it again instead of being rejected as a duplicate.

Requires Drive: with the integration off there is nothing to reconcile against.
"""

from dataclasses import dataclass, field
from typing import Callable, List, Optional

from . import drive, history, notion_sync


def _noop(*_args, **_kwargs) -> None:
    pass


@dataclass
class CheckSummary:
    total: int = 0
    removed: int = 0
    intact: int = 0
    unchecked: int = 0
    kept_for_notion: int = 0
    cancelled: bool = False
    removed_names: List[str] = field(default_factory=list)


def check_sync(
    cfg,
    log: Callable[[str], None] = _noop,
    progress: Callable[[int, int], None] = _noop,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> CheckSummary:
    summary = CheckSummary()
    cancelled = should_cancel or (lambda: False)

    if not cfg.enable_drive:
        log("[중단] Google Drive 연동이 꺼져 있어 동기화 점검을 할 수 없습니다.")
        return summary

    history_data = history.load_history()
    summary.total = len(history_data)
    if not history_data:
        log("[정보] 점검할 변환 기록이 없습니다.")
        return summary

    log("[1/2] Google Drive 인증 중...")
    service = drive.get_google_drive_service(log=log)
    if not service:
        log("[중단] Google Drive 인증에 실패했습니다.")
        return summary

    notion = notion_sync.build_notion_client(cfg, log=log)
    if notion:
        log("[정보] Notion 연동이 활성화되어 삭제가 함께 반영됩니다.")

    log(f"[2/2] 기록 {len(history_data)}건을 점검합니다.\n")

    hashes_to_remove = []
    items = list(history_data.items())

    for index, (file_hash, record) in enumerate(items):
        if cancelled():
            summary.cancelled = True
            log("[취소] 사용자가 점검을 중단했습니다.")
            break

        progress(index, len(items))
        original_name = record.get("original_name", "Unknown")
        drive_file_id = record.get("drive_file_id")
        notion_page_id = record.get("notion_page_id")

        if not drive_file_id:
            log(f"▶ {original_name}: Drive ID가 없어 건너뜁니다.")
            summary.unchecked += 1
            continue

        log(f"▶ {original_name}")
        deleted = drive.check_file_deleted(service, drive_file_id)

        if deleted is None:
            # Could not determine -- never prune history on an inconclusive check.
            log("  - [보류] 상태를 확인하지 못했습니다. 기록을 유지합니다.")
            summary.unchecked += 1
            continue

        if not deleted:
            log("  - [정상] Drive에 그대로 있습니다.")
            summary.intact += 1
            continue

        log("  - [감지] Drive에서 삭제되었습니다.")

        # The history entry is the only reference to the Notion page, so it
        # must only be pruned once there is nothing left to clean up: either
        # there was never a linked page, or archiving it actually succeeded.
        # Otherwise (Notion off/unconfigured, or the archive call failed) the
        # record is kept so the page isn't orphaned with no way to find it.
        notion_cleanup_done = True
        if notion_page_id:
            if notion:
                if notion_sync.archive_notion_page(notion, notion_page_id, log=log):
                    log("  - [완료] 연결된 Notion 페이지를 보관 처리했습니다.")
                else:
                    notion_cleanup_done = False
                    log("  - [보류] Notion 페이지 보관에 실패해 기록을 유지합니다.")
            else:
                notion_cleanup_done = False
                log("  - [보류] Notion 연동이 꺼져 있어 연결된 페이지를 정리할 수 없어 기록을 유지합니다.")
        else:
            log("  - [정보] 연결된 Notion 페이지가 없습니다.")

        if notion_cleanup_done:
            hashes_to_remove.append(file_hash)
            summary.removed_names.append(original_name)
        else:
            summary.kept_for_notion += 1

    if hashes_to_remove:
        history.remove_history_entries(hashes_to_remove)
        summary.removed = len(hashes_to_remove)
        log(f"\n[결과] 삭제된 기록 {summary.removed}건을 정리했습니다.")
    else:
        log("\n[결과] 삭제된 파일이 없습니다. 모두 동기화 상태입니다.")

    if summary.kept_for_notion:
        log(f"[정보] 연결된 Notion 페이지 정리가 필요해 유지한 기록 {summary.kept_for_notion}건이 있습니다.")

    progress(len(items), len(items))
    return summary
