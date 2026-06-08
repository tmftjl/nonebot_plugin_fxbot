"""跨适配器图片来源读取。"""

from __future__ import annotations

import base64
from pathlib import Path

from nonebot.adapters import Bot, Event

from ..utils.http import get_shared_async_client
from .message import (
    event_message,
    extract_raw_image_sources,
    extract_reply_message_id,
    get_replied_message,
)
from .support import is_qq_official


async def fetch_image_bytes(src: str | bytes) -> bytes | None:
    """读取图片来源内容。"""
    if isinstance(src, bytes):
        return src
    value = str(src or "").strip()
    if not value:
        return None
    if value.startswith("base64://"):
        try:
            return base64.b64decode(value[len("base64://") :])
        except Exception:
            return None
    if value.startswith(("http://", "https://")):
        try:
            client = await get_shared_async_client()
            response = await client.get(value, follow_redirects=True)
            response.raise_for_status()
            return response.content
        except Exception:
            return None
    try:
        return Path(value).read_bytes()
    except Exception:
        return None


async def image_sources_from_event_or_reply(bot: Bot, event: Event) -> list[str | bytes]:
    """从当前消息或回复消息中提取图片来源。"""
    message = event_message(event)
    sources = extract_raw_image_sources(message)
    if sources or is_qq_official(bot):
        return sources

    reply = getattr(event, "reply", None)
    reply_message = getattr(reply, "message", None) if reply else None
    sources = extract_raw_image_sources(reply_message)
    if sources:
        return sources

    reply_id = extract_reply_message_id(message)
    if reply_id is None:
        return []
    try:
        replied = await get_replied_message(bot, reply_id)
    except Exception:
        return []
    return extract_raw_image_sources(replied)
