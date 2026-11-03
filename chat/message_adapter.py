"""将 NoneBot MessageEvent 适配为 ChatRequest。"""

from __future__ import annotations

import io
import json
import base64
import asyncio
import hashlib
from uuid import uuid4
from typing import Any
from datetime import datetime, timezone
from dataclasses import dataclass

from PIL import Image, ImageOps
from nonebot import logger

from .types import MentionRef, ChatRequest, TriggerKind, InboundSegment
from .voice import transcribe_event
from ..config import get_manager as get_config_manager
from ..adapter import (
    ReplyInfo,
    normalize_id,
    event_is_tome,
    event_message,
    event_user_id,
    event_group_id,
    event_user_name,
    event_plain_text,
    event_sender_role,
    extract_forward_ids,
    get_platform_adapter,
    extract_raw_emoji_sources,
    extract_raw_image_sources,
)
from ..utils.tz import now, to_naive_utc
from ..utils.http import ResponseTooLargeError, read_limited, ensure_public_http_url, get_shared_async_client
from ..utils.cache import cache
from .message_context import quote_preview

_MAX_IMAGE_COUNT = 64
_MAX_IMAGE_BYTES = 16 * 1024 * 1024
_MAX_IMAGE_SIDE = 512
_MAX_IMAGE_BYTES_SENT = 1024 * 1024
_MAX_REDIRECTS = 3
_MAX_QUOTED_CHARS = 500
_MAX_MENTIONS = 10
_MAX_NAME_CHARS = 32
_IMAGE_SIDES = {"normal": 512, "high": 1024}
_DEFAULT_LIMIT = object()
_OVERSIZED_READ_BYTES = 32 * 1024 * 1024


def _visual() -> dict[str, Any]:
    """读取视觉配置；读取失败按空配置处理。"""
    try:
        value = get_config_manager().get_system()["chat"].get("visual") or {}
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _max_image_count() -> int:
    """单条消息最多处理的图片数；0 表示不接收图片。"""
    value = _visual().get("max_image_num", _MAX_IMAGE_COUNT)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return _MAX_IMAGE_COUNT
    return value


def _max_image_bytes() -> int | None:
    """图片大小上限；0 表示不限。"""
    value = _visual().get("max_image_size_mb", _MAX_IMAGE_BYTES / 1024 / 1024)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        value = _MAX_IMAGE_BYTES / 1024 / 1024
    return int(value * 1024 * 1024) or None


def _max_image_side() -> int:
    """图片最长边；normal 512，high 1024。"""
    quality = str(_visual().get("image_quality") or "normal")
    return _IMAGE_SIDES.get(quality, _MAX_IMAGE_SIDE)


def _handle_oversized() -> bool:
    """过大图片是否处理；关闭时仍按大小上限直接跳过。"""
    return bool(_visual().get("handle_oversized_images", True))


def _oversized_method() -> str:
    """过大图片处理方式：compress 或 discard。"""
    method = str(_visual().get("oversized_image_handle_method") or "compress")
    return method if method in {"compress", "discard"} else "compress"


def _download_limit(limit: int | None, *, compress_oversized: bool) -> int | None:
    """压缩模式下先按硬上限读入，再压到配置大小。"""
    if limit is None or not compress_oversized or not _handle_oversized() or _oversized_method() != "compress":
        return limit
    return max(limit, _OVERSIZED_READ_BYTES)


def _compress_to_limit(data: bytes, limit: int) -> bytes:
    """把过大图片按画质和尺寸逐级压到目标字节内；失败返回原数据。"""
    try:
        image = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
        image.load()
    except Exception:
        return data
    if image.mode in {"RGBA", "LA", "P"}:
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.getchannel("A"))
        image = background
    elif image.mode != "RGB":
        image = image.convert("RGB")
    original = image.copy()
    for side in (_MAX_IMAGE_SIDE, 1024, 768, 512, 384, 256, 160):
        candidate = original.copy()
        candidate.thumbnail((side, side))
        for quality in (85, 70, 55, 40):
            buffer = io.BytesIO()
            candidate.save(buffer, format="JPEG", quality=quality, optimize=True)
            result = buffer.getvalue()
            if result and len(result) <= limit:
                return result
    return data


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


def _compress_image(data: bytes) -> bytes:
    """缩小过大的图片，减少模型请求体积；无法解码时原样返回。"""
    try:
        image = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
        image.load()
    except Exception:
        return data
    side = _max_image_side()
    if max(image.size) <= side and len(data) <= _MAX_IMAGE_BYTES_SENT:
        return data
    if image.mode in {"RGBA", "LA", "P"}:
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.getchannel("A"))
        image = background
    elif image.mode != "RGB":
        image = image.convert("RGB")
    image.thumbnail((side, side))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=85, optimize=True)
    return buffer.getvalue() or data


async def _fetch_remote_image(url: str, limit: int | None | object = _DEFAULT_LIMIT) -> bytes:
    """下载公网图片：逐跳校验重定向，流式读取并限制大小。"""
    if limit is _DEFAULT_LIMIT:
        limit = _max_image_bytes()
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
            # 0 表示不限模型侧大小，但读取仍保留硬上限，避免超大响应撑爆内存
            return await read_limited(
                response.aiter_bytes(), response.headers.get("content-length"), limit or 32 * 1024 * 1024
            )
    raise ValueError("重定向次数过多")


async def _load_image(
    source: str | bytes,
    limit: int | None | object = _DEFAULT_LIMIT,
    *,
    compress_oversized: bool = True,
) -> bytes:
    """加载内联字节、图片 data URL、base64 或公网图片，拒绝本地路径。"""
    if limit is _DEFAULT_LIMIT:
        limit = _max_image_bytes()
    read_limit = _download_limit(limit, compress_oversized=compress_oversized)
    if isinstance(source, bytes):
        data = source
    else:
        value = str(source or "").strip()
        if value.startswith(("data:", "base64://")):
            if value.startswith("data:"):
                header, separator, encoded = value.partition(",")
                if not separator or not header.startswith("data:image/") or not header.endswith(";base64"):
                    raise ValueError("内联图片必须使用图片 MIME 类型与 base64 编码")
            else:
                encoded = value[9:]
            if read_limit is not None and len(encoded) > read_limit * 4 // 3 + 4:
                raise ResponseTooLargeError("base64 图片超过大小上限")
            data = base64.b64decode(encoded, validate=True)
        elif value:
            data = await _fetch_remote_image(value, limit=read_limit)
        else:
            raise ValueError("图片源为空")
    if limit is not None and len(data) > limit:
        if compress_oversized and _handle_oversized() and _oversized_method() == "compress":
            data = await asyncio.to_thread(_compress_to_limit, data, limit)
        if len(data) > limit:
            raise ResponseTooLargeError(f"图片超过大小上限：{len(data)} > {limit}")
    return data


async def load_image_bytes(source: str | bytes, *, limit: int | None | object = _DEFAULT_LIMIT) -> bytes:
    """按调用方上限加载图片原始字节；表情采集用自己的大小配置，不压缩。"""
    return await _load_image(source, limit=limit, compress_oversized=False)


def emoji_sources_for_event(event: Any) -> list[str | bytes]:
    """提取消息里的表情包图片源，不下载或解码。"""
    sources = extract_raw_emoji_sources(event_message(event))
    return list(dict.fromkeys(source for source in sources if source))


def image_sources_for_event(event: Any, reply: ReplyInfo | None = None) -> list[str | bytes]:
    """提取消息或引用中的原始图片源，不下载或解码。"""
    sources = extract_raw_image_sources(event_message(event))
    if not sources and reply is not None:
        sources = list(reply.image_sources)
    return list(dict.fromkeys(source for source in sources if source))[: _max_image_count()]


def memory_image_sources(event: Any) -> list[tuple[str, str | bytes]]:
    """图片记忆只登记当前消息原图，保留原始组件下标并排除表情。"""
    result = []
    emojis = set(emoji_sources_for_event(event))
    for index, segment in enumerate(list(event_message(event) or [])):
        for source in extract_raw_image_sources([segment]):
            if source and source not in emojis:
                result.append((str(index), source))
    return result


async def load_images(sources: list[str | bytes]) -> list[str]:
    """把原始图片源加载为模型可消费的 data URL。"""

    images: list[str] = []
    seen: set[str | bytes] = set()
    limit = _max_image_count()
    for source in sources:
        if len(images) >= limit:
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
            data = await asyncio.to_thread(_compress_image, data)
            images.append(f"data:{_image_mime(data)};base64,{base64.b64encode(data).decode('ascii')}")
    return images


def _quote_snapshot(reply: ReplyInfo | None) -> dict[str, str] | None:
    """把适配器的引用结果压成可持久化的短预览。"""
    if reply is None:
        return None
    return {
        "message_id": str(reply.message_id) if reply.message_id is not None else "",
        "sender_id": str(reply.sender_id or ""),
        "sender_name": " ".join(str(reply.sender_name or "").split())[:32],
        "text": " ".join(" ".join(str(part) for part in reply.texts).split())[:_MAX_QUOTED_CHARS],
    }


def _quoted_text(reply: ReplyInfo | None) -> str:
    """提取引用消息正文和发言人；压成单行并限长，避免被用来伪造多段消息。"""
    return quote_preview(_quote_snapshot(reply))


async def _reply_info(bot: Any, event: Any) -> ReplyInfo | None:
    """读取引用消息信息，失败时按无引用处理。"""
    try:
        adapter = get_platform_adapter(bot)
        _, target = adapter.reply_target(event)
        if target is None and not adapter.has_reply_reference(event):
            return None
        key = (
            "chat_reply_info",
            session_id_for_event(bot, event),
            adapter.event_message_id(event) or id(event),
            str(target),
        )
        saved = cache.get(key)
        if saved is not None:
            return saved["info"]
        info = await adapter.get_reply_info(bot, event)
        cache.set(key, {"info": info}, ttl=60)
        return info
    except Exception:
        logger.opt(exception=True).warning("[Chat] 读取引用消息失败")
        return None


async def read_quote_snapshot(bot: Any, event: Any) -> dict[str, str] | None:
    """普通上下文和 Focus 共用同一次引用读取。"""
    return _quote_snapshot(await _reply_info(bot, event))


def _segment_name(segment: Any) -> str:
    """从消息段里读昵称；QQ 官方平台会随 @ 段带 username，OneBot 通常没有。"""
    data = getattr(segment, "data", {}) or {}
    for key in ("username", "nickname", "name", "card"):
        value = " ".join(str(data.get(key) or "").split())[:_MAX_NAME_CHARS]
        if value:
            return value
    return ""


def _self_ids(bot: Any) -> set[str]:
    """Bot 自身可能的 ID；QQ 官方平台 self_id 与 openid 不是一回事。"""
    values = {str(getattr(bot, "self_id", "") or "")}
    info_id = getattr(getattr(bot, "self_info", None), "id", None)
    if info_id is not None:
        values.add(str(info_id))
    values.discard("")
    return values


def mention_flags(bot: Any, event: Any) -> tuple[bool, bool]:
    """同步读取提及 Bot 和其他成员的标记，忽略全体提及。"""
    try:
        if not event_group_id(event):
            return event_is_tome(event), False
        targets = set(get_platform_adapter(bot).mention_targets(event_message(event), {"all"}))
        self_ids = _self_ids(bot)
        return event_is_tome(event) or bool(targets & self_ids), bool(targets - self_ids)
    except Exception:
        logger.opt(exception=True).debug("[Chat] 读取提及标记失败")
        return False, False


async def _member_name(bot: Any, group_id: str, user_id: str, event: Any) -> str:
    """查询 @ 目标昵称，失败时只保留 ID。"""
    try:
        value = await get_platform_adapter(bot).get_group_member_name(bot, group_id, user_id, event)
    except Exception:
        return ""
    return " ".join(str(value or "").split())[:_MAX_NAME_CHARS]


async def collect_mentions(
    bot: Any,
    event: Any,
    group_id: str | None,
    *,
    resolve_names: bool = True,
) -> list[MentionRef]:
    """收集消息里被 @ 的成员，排除 Bot 自己和 @全体成员。"""
    if not group_id:
        return []
    try:
        adapter = get_platform_adapter(bot)
        segments = list(event_message(event) or [])
    except Exception:
        return []

    self_ids = _self_ids(bot)
    refs: list[MentionRef] = []
    seen: set[str] = set()
    for segment in segments:
        try:
            target = str(adapter.mention_target(segment) or "").strip()
        except Exception:
            continue
        if not target or target == "all" or target in seen or target in self_ids:
            continue
        seen.add(target)
        refs.append(MentionRef(user_id=target, name=_segment_name(segment)))
        if len(refs) >= _MAX_MENTIONS:
            break

    missing = [ref for ref in refs if resolve_names and not ref.name]
    if missing:
        names = await asyncio.gather(*(_member_name(bot, group_id, ref.user_id, event) for ref in missing))
        for ref, name in zip(missing, names):
            ref.name = name
    return refs


def message_text(event: Any) -> str:
    """纯文本；消息只有合并转发时补一个占位，让 AI 知道可以展开。"""
    text = event_plain_text(event)
    if text:
        return text
    return "[合并转发消息]" if extract_forward_ids(event_message(event)) else ""


def platform_for_bot(bot: Any) -> str:
    """读取适配器平台标识，字段不可用时返回占位。"""
    adapter = getattr(bot, "adapter", None)
    get_name = getattr(adapter, "get_name", None)
    return str(get_name() or "unknown") if callable(get_name) else "unknown"


def session_id_for_event(bot: Any, event: Any) -> str:
    """按平台、Bot 和群或用户生成隔离的会话标识。"""
    group_id = normalize_id(event_group_id(event))
    user_id = normalize_id(event_user_id(event)) or "unknown"
    namespace = f"{platform_for_bot(bot)}:{getattr(bot, 'self_id', None) or 'unknown'}"
    return f"{namespace}:group:{group_id}" if group_id else f"{namespace}:private:{user_id}"


@dataclass(frozen=True)
class MessageIdentity:
    """平台无关的入站消息身份。"""

    session_id: str
    event_id: str | None
    message_id: str | None
    reply_to_message_id: str | None
    dedupe_key: str
    platform: str = ""


def event_identity(bot: Any, event: Any) -> MessageIdentity:
    """读取消息身份；无 ID 且无内容时的 UUID 不保证幂等。"""
    adapter = get_platform_adapter(bot)
    platform = platform_for_bot(bot)
    session_id = session_id_for_event(bot, event)
    event_id = adapter.event_id(event)
    message_id = adapter.event_message_id(event)
    _, reply_id = adapter.reply_target(event)
    if message_id:
        dedupe_key = f"msg:{message_id}"
    elif event_id:
        dedupe_key = f"event:{event_id}"
    else:
        text = event_plain_text(event)
        sources = extract_raw_image_sources(event_message(event))
        if text or sources:
            content = {
                "platform": platform,
                "session_id": session_id,
                "user_id": event_user_id(event),
                "text": text,
                "images": [
                    {"bytes": base64.b64encode(source).decode("ascii")}
                    if isinstance(source, bytes)
                    else {"source": source}
                    for source in sources
                ],
            }
            payload = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            dedupe_key = f"content:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"
        else:
            dedupe_key = f"uuid:{uuid4().hex}"
    return MessageIdentity(
        session_id,
        event_id,
        message_id,
        str(reply_id) if reply_id is not None else None,
        dedupe_key,
        platform,
    )


def message_occurred_at(event: Any, received_at: datetime | None = None) -> datetime:
    """提取平台原始时间，普通历史消息和触发消息使用相同时间语义。"""
    occurred_at = received_at if received_at is not None else to_naive_utc(now())
    event_time = getattr(event, "time", None)
    if isinstance(event_time, (int, float)) and not isinstance(event_time, bool):
        try:
            occurred_at = to_naive_utc(datetime.fromtimestamp(event_time, tz=timezone.utc))
        except (ValueError, OverflowError, OSError):
            pass
    return occurred_at


async def adapt_message_event(bot: Any, event: Any, *, trigger_kind: TriggerKind) -> ChatRequest:
    """适配 NoneBot 消息事件。"""
    received_at = to_naive_utc(now())
    occurred_at = message_occurred_at(event, received_at)
    user_id = normalize_id(event_user_id(event)) or ""
    group_id = normalize_id(event_group_id(event))
    identity = event_identity(bot, event)
    segments: list[InboundSegment] = []
    try:
        for seg in event.get_message():
            segments.append(InboundSegment(type=str(seg.type), data=dict(seg.data)))
    except Exception:
        pass
    reply, mentions, voice_text = await asyncio.gather(
        _reply_info(bot, event),
        collect_mentions(bot, event, group_id),
        transcribe_event(event),
    )
    text = " ".join(part for part in (message_text(event), voice_text) if part)
    return ChatRequest(
        session_id=identity.session_id,
        turn_id=uuid4().hex,
        dedupe_key=identity.dedupe_key,
        trigger_kind=trigger_kind,
        proactive=trigger_kind == "proactive",
        event_id=identity.event_id,
        message_id=identity.message_id,
        reply_to_message_id=identity.reply_to_message_id,
        occurred_at=occurred_at,
        user_id=user_id,
        user_name=event_user_name(event, user_id),
        group_id=group_id,
        text=text,
        segments=segments,
        images=await load_images(image_sources_for_event(event, reply)),
        quoted_text=_quoted_text(reply),
        platform=platform_for_bot(bot),
        metadata={
            "event": event,
            "received_at": received_at.isoformat(),
            "sender_role": event_sender_role(bot, event),
            "quote_snapshot": _quote_snapshot(reply),
            "raw_images": [source for _, source in memory_image_sources(event)],
            "raw_image_paths": [path for path, _ in memory_image_sources(event)],
        },
        mentions=mentions,
    )
