"""将 NoneBot MessageEvent 适配为 ChatRequest。"""

from __future__ import annotations

import base64
from typing import Any

from nonebot import logger

from .types import ChatRequest, InboundSegment
from ..adapter import (
    normalize_id,
    event_user_id,
    event_group_id,
    event_plain_text,
    fetch_image_bytes,
    image_sources_from_event_or_reply,
)

_MAX_IMAGE_COUNT = 4
_MAX_IMAGE_BYTES = 5 * 1024 * 1024


def _image_mime(data: bytes) -> str:
    """按文件头识别图片类型，供模型接口构造 data URL。"""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"BM"):
        return "image/bmp"
    return "image/jpeg"


async def _resolve_images(bot: Any, event: Any) -> list[str]:
    """把消息或引用中的图片转成模型可消费的 data URL。"""
    try:
        sources = await image_sources_from_event_or_reply(bot, event)
    except Exception:
        logger.opt(exception=True).warning("[Chat] 读取消息图片失败")
        return []

    images: list[str] = []
    seen: set[str | bytes] = set()
    for source in sources:
        if len(images) >= _MAX_IMAGE_COUNT:
            break
        key: str | bytes = source if isinstance(source, bytes) else str(source or "")
        if not key or key in seen:
            continue
        seen.add(key)
        try:
            data = await fetch_image_bytes(source)
        except Exception:
            logger.opt(exception=True).warning("[Chat] 下载消息图片失败")
            continue
        if not data:
            continue
        if len(data) > _MAX_IMAGE_BYTES:
            logger.warning(f"[Chat] 图片超过 {_MAX_IMAGE_BYTES} 字节，已跳过")
            continue
        images.append(f"data:{_image_mime(data)};base64,{base64.b64encode(data).decode('ascii')}")
    return images


def session_id_for_event(event: Any) -> str:
    """按群或用户生成会话标识。"""
    group_id = normalize_id(event_group_id(event))
    user_id = normalize_id(event_user_id(event)) or ""
    return f"group:{group_id}" if group_id else f"private:{user_id}"


async def adapt_message_event(bot: Any, event: Any) -> ChatRequest:
    """适配 NoneBot 消息事件。"""
    user_id = normalize_id(event_user_id(event)) or ""
    group_id = normalize_id(event_group_id(event))
    session_id = session_id_for_event(event)
    segments: list[InboundSegment] = []
    try:
        for seg in event.get_message():
            segments.append(InboundSegment(type=str(seg.type), data=dict(seg.data)))
    except Exception:
        pass
    return ChatRequest(
        session_id=session_id,
        user_id=user_id,
        group_id=group_id,
        text=event_plain_text(event),
        segments=segments,
        images=await _resolve_images(bot, event),
        platform=event.get_type() if hasattr(event, "get_type") else "",
        metadata={"event": event},
    )
