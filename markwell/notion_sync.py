"""Notion database sync: chunked markdown page creation, archival, client setup."""

from typing import Callable, Optional, Tuple

from notion_client import Client


def _noop(msg: str) -> None:
    pass


def _code_block(chunk: str) -> dict:
    return {
        "object": "block",
        "type": "code",
        "code": {
            "rich_text": [{"type": "text", "text": {"content": chunk}}],
            "language": "markdown"
        }
    }


def sync_to_notion(notion_client, db_id: str, file_name: str, drive_link: Optional[str],
                    md_content: str = "", log: Callable[[str], None] = _noop) -> Tuple[bool, Optional[str]]:
    if not notion_client or not db_id:
        return False, None

    try:
        # Chunk text to avoid Notion's 2000 character limit per block.
        # 1900 leaves margin for newline (\r\n) or unicode length calculation differences.
        chunk_size = 1900
        max_blocks_per_call = 100  # Notion API limit, applies to create AND append calls
        all_chunks = (
            [md_content[i:i + chunk_size] for i in range(0, len(md_content), chunk_size)]
            if md_content else []
        )
        first_chunks = all_chunks[:max_blocks_per_call]
        remaining_chunks = all_chunks[max_blocks_per_call:]
        children = [_code_block(c) for c in first_chunks]

        properties = {
            "Name": {"title": [{"text": {"content": file_name}}]},
        }
        # drive_link may be None when Google Drive sync is disabled -- omit the
        # URL property entirely rather than sending {"url": None}.
        if drive_link:
            properties["URL"] = {"url": drive_link}

        new_page = {
            "parent": {"database_id": db_id},
            "properties": properties,
            "children": children
        }
        response = notion_client.pages.create(**new_page)
        page_id = response.get("id")

        if remaining_chunks:
            omitted_chars = sum(len(c) for c in remaining_chunks)
            log(
                f"[WARNING] '{file_name}' 문서가 {max_blocks_per_call}블록({chunk_size}자 기준)을 "
                f"초과합니다. 남은 {len(remaining_chunks)}블록({omitted_chars}자)을 이어붙이는 중..."
            )
            try:
                for start in range(0, len(remaining_chunks), max_blocks_per_call):
                    batch = remaining_chunks[start:start + max_blocks_per_call]
                    notion_client.blocks.children.append(
                        block_id=page_id,
                        children=[_code_block(c) for c in batch],
                    )
            except Exception as e:
                log(
                    f"[WARNING] '{file_name}' 문서의 나머지 {len(remaining_chunks)}블록"
                    f"({omitted_chars}자)을 추가하지 못했습니다: {e}"
                )

        return True, page_id
    except Exception as e:
        log(f"[ERROR] Failed to sync to Notion: {str(e)}")
        log("Note: Ensure your Notion Database has a Title property named 'Name' and a URL property named 'URL'.")
        return False, None


def archive_notion_page(notion_client, page_id: str, log: Callable[[str], None] = _noop) -> bool:
    try:
        notion_client.pages.update(page_id=page_id, archived=True)
        return True
    except Exception as e:
        log(f"[ERROR] Failed to archive Notion page: {e}")
        return False


def build_notion_client(cfg, log: Callable[[str], None] = _noop):
    if not cfg.notion_configured:
        return None
    try:
        return Client(auth=cfg.notion_api_key)
    except Exception as e:
        log(f"[WARNING] Failed to initialize Notion client: {e}")
        return None
