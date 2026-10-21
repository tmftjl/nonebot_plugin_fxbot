"""将 NoneBot MessageEvent 适配为 ChatRequest。"""

from __future__ import annotations

import base64
from typing import Any

from nonebot import logger

from .types import ChatRequest, InboundSegment
from ..adapter import (
    ReplyInfo,
    normalize_id,
    event_message,
    event_user_id,
    event_group_id,
    event_user_name,
    event_plain_text,
    get_platform_adapter,
    extract_raw_image_sources,
)
from ..utils.http import ResponseTooLargeError, read_limited, ensure_public_http_url, get_shared_async_client

_MAX_IMAGE_COUNT = 4
_MAX_IMAGE_BYTES = 5 * 1024 * 1024
_MAX_REDIRECTS = 3
_MAX_QUOTED_CHARS = 500


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


async def _fetch_remote_image(url: str) -> bytes:
    """下载公网图片：逐跳校验重定向，流式读取并限制大小。"""
    client = await get_shared_async_client()
    for _ in range(_MAX_REDIRECTS + 1):
        await ensure_public_http_url(url)
        async with client.stream("GET", url, follow_redirects=False) as response:
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    raise ValueError("重定向缺少 Location")
                url = str(response.url.join(location))
                continue
            response.raise_for_status()
            return await read_limited(response.aiter_bytes(), response.headers.get("content-length"), _MAX_IMAGE_BYTES)
    raise ValueError("重定向次数过多")


async def _load_image(source: str | bytes) -> bytes:
    """加载图片源；只接受内联字节、base64 和公网 http(s) URL，拒绝本地路径。"""
    if isinstance(source, bytes):
        data = source
    else:
        value = str(source or "").strip()
        if value.startswith("base64://"):
            encoded = value[9:]
            if len(encoded) > _MAX_IMAGE_BYTES * 4 // 3 + 4:
                raise ResponseTooLargeError("base64 图片超过大小上限")
            data = base64.b64decode(encoded)
        elif value:
            return await _fetch_remote_image(value)
        else:
            raise ValueError("图片源为空")
    if len(data) > _MAX_IMAGE_BYTES:
        raise ResponseTooLargeError(f"图片超过大小上限：{len(data)} > {_MAX_IMAGE_BYTES}")
    return data


async def resolve_images(event: Any, reply: ReplyInfo | None = None) -> list[str]:
    """把消息（或引用）中的图片转成模型可消费的 data URL。"""
    sources = extract_raw_image_sources(event_message(event))
    if not sources and reply is not None:
        sources = list(reply.image_sources)

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
            data = await _load_image(source)
        except Exception as exc:
            logger.warning(f"[Chat] 图片加载失败，已跳过: {exc}")
            continue
        if data:
            images.append(f"data:{_image_mime(data)};base64,{base64.b64encode(data).decode('ascii')}")
    return images


def _quoted_text(reply: ReplyInfo | None) -> str:
    """提取引用消息正文；压成单行并限长，避免被用来伪造多段消息。"""
    if reply is None:
        return ""
    text = " ".join(" ".join(str(part) for part in reply.texts).split())
    return text[:_MAX_QUOTED_CHARS]


async def _reply_info(bot: Any, event: Any) -> ReplyInfo | None:
    """读取引用消息信息，失败时按无引用处理。"""
    try:
        return await get_platform_adapter(bot).get_reply_info(bot, event)
    except Exception:
        logger.opt(exception=True).warning("[Chat] 读取引用消息失败")
        return None


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
    reply = await _reply_info(bot, event)
    return ChatRequest(
        session_id=session_id,
        user_id=user_id,
        user_name=event_user_name(event, user_id),
        group_id=group_id,
        text=event_plain_text(event),
        segments=segments,
        images=await resolve_images(event, reply),
        quoted_text=_quoted_text(reply),
        platform=event.get_type() if hasattr(event, "get_type") else "",
        metadata={"event": event},
    )
