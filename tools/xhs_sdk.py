"""
XHS (Xiaohongshu) tool.
Reads: xhs SDK (cookie-based, already installed).
Writes: generate step-by-step instructions for computer-use MCP execution.
"""

import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


def search_notes(keyword: str, limit: int = 10) -> list[dict]:
    """
    Search XHS notes by keyword using the xhs SDK.
    Requires XHS_COOKIES environment variable (cookie string from browser).
    """
    cookies_raw = os.environ.get("XHS_COOKIES", "")
    if not cookies_raw:
        logger.warning("XHS_COOKIES not set — XHS search unavailable")
        return []

    try:
        from xhs import XhsClient
        cookies = _parse_cookies(cookies_raw)
        client = XhsClient(cookie=cookies_raw)
        results = client.get_note_by_keyword(keyword=keyword, page=1)
        notes = []
        for item in results.get("items", [])[:limit]:
            note_card = item.get("note_card", {})
            notes.append({
                "id": item.get("id", ""),
                "title": note_card.get("title", ""),
                "desc": note_card.get("desc", ""),
                "liked_count": note_card.get("interact_info", {}).get("liked_count", "0"),
                "url": f"https://www.xiaohongshu.com/explore/{item.get('id', '')}",
            })
        return notes
    except Exception as exc:
        logger.warning("XHS SDK search failed: %s", exc)
        return []


def _parse_cookies(raw: str) -> dict:
    """Parse a cookie string into a dict."""
    cookies = {}
    for part in raw.split(";"):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            cookies[k.strip()] = v.strip()
    return cookies


def generate_post_instructions(title: str, body: str, hashtags: list[str]) -> str:
    """
    Generate step-by-step instructions for posting to XHS via computer-use.
    These instructions are meant to be executed by Claude Code's computer-use MCP.
    """
    tags_str = " ".join(f"#{t}" for t in hashtags)
    full_text = f"{title}\n\n{body}\n\n{tags_str}"

    instructions = f"""
XHS Post Instructions (execute via computer-use):

1. Take screenshot to verify Chrome is showing XHS (xiaohongshu.com)
   - If not, click address bar, type https://www.xiaohongshu.com, press Enter
   - Wait 3s, take screenshot

2. Click the "+" or "发布笔记" button (usually top-right or center of page)
   - Wait 2s, take screenshot

3. Select "文字笔记" (text note) if prompted to choose type
   - Wait 1.5s

4. Click the title field and type the title:
   {title}

5. Click the body/content field and type:
   {body}

6. Add hashtags at the end of the content:
   {tags_str}

7. Click "发布" (publish button)
   - Wait 3s, take screenshot to verify success

Full content to post:
---
{full_text}
---

Human-like timing: randomize waits between 1.5-3.5s between actions.
"""
    return instructions.strip()
