"""消息适配器注册与通用入口。"""

from __future__ import annotations

import base64
from pathlib import Path
from re import Match, Pattern
from typing import Any

from .bot import PlatformAdapter
from .events import extract_message_target
from .registry import adapter_name, get_platform_adapter, register_adapter

MessageAdapter = PlatformAdapter
register_message_adapter = register_adapter
require_message_adapter = get_platform_adapter


def get_message_adapter(bot: Any) -> MessageAdapter:
    return get_platform_adapter(bot)


async def fetch_image_bytes(src: str | bytes) -> bytes | None:
    if isinstance(src, bytes):
        return src
    value = str(src or "").strip()
    if not value:
        return None
    if value.startswith("base64://"):
        try:
            return base64.b64decode(value[9:])
        except Exception:
            return None
    if value.startswith(("http://", "https://")):
        try:
            from ...utils.http import get_shared_async_client

            response = await (await get_shared_async_client()).get(value, follow_redirects=True)
            response.raise_for_status()
            return response.content
        except Exception:
            return None
    try:
        return Path(value).read_bytes()
    except Exception:
        return None


async def image_sources_from_event_or_reply(bot: Any, event: Any) -> list[str | bytes]:
    message = event_message(event)
    sources = extract_raw_image_sources(message)
    if sources:
        return sources
    reply = getattr(event, "reply", None)
    sources = extract_raw_image_sources(getattr(reply, "message", None) if reply else None)
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


def build_message_segment(bot: Any, seg_type: str, data: Any = None) -> Any:
    """根据适配器构造消息段。"""
    return require_message_adapter(bot).build_segment(bot, seg_type, data)


def build_message(bot: Any, *segments: Any) -> Any:
    """根据适配器构造消息对象。"""
    filtered = [segment for segment in segments if segment is not None]
    return get_message_adapter(bot).build_message(bot, filtered)


def event_message(event: Any) -> Any:
    """提取事件消息对象。"""
    get_message = getattr(event, "get_message", None)
    if callable(get_message):
        try:
            return get_message()
        except (AttributeError, ValueError):
            return None
    return getattr(event, "message", None)


def iter_message_segments(message: Any) -> list[Any]:
    """遍历消息段。"""
    if message is None:
        return []
    if isinstance(message, (str, bytes)):
        return [message]
    try:
        return list(message)
    except TypeError:
        return []


def segment_type(segment: Any) -> str:
    """提取消息段类型。"""
    if isinstance(segment, dict):
        return str(segment.get("type") or "")
    return str(getattr(segment, "type", "") or "")


def segment_data(segment: Any) -> dict[str, Any]:
    """提取消息段数据。"""
    if isinstance(segment, dict):
        data = segment.get("data") or {}
    else:
        data = getattr(segment, "data", {}) or {}
    return data if isinstance(data, dict) else {}


def segment_text(segment: Any) -> str:
    """提取文本消息段内容。"""
    if isinstance(segment, str):
        return segment
    seg_type = segment_type(segment)
    if seg_type and seg_type not in {"text", "plain"}:
        return ""
    if hasattr(segment, "is_text"):
        try:
            if not segment.is_text():
                return ""
        except Exception:
            return ""
    data = segment_data(segment)
    for key in ("text", "content"):
        value = data.get(key)
        if value is not None:
            return str(value)
    return str(segment) if seg_type in {"text", "plain"} else ""


def is_text_segment(segment: Any) -> bool:
    """判断消息段是否为文本。"""
    if isinstance(segment, str):
        return True
    if segment_type(segment) in {"text", "plain"}:
        return True
    if hasattr(segment, "is_text"):
        try:
            return bool(segment.is_text())
        except Exception:
            return False
    return False


def _make_text_segments(message: Any, original_segment: Any, text: str) -> list[Any]:
    """用当前消息类型重新构造文本段。"""
    if isinstance(original_segment, str):
        return [text]

    try:
        data = segment_data(original_segment)
        if "text" in data:
            data["text"] = text
            return [original_segment]
        elif "content" in data:
            data["content"] = text
            return [original_segment]
    except Exception:
        pass

    try:
        segments = list(message.__class__(text))
        if segments:
            return segments
    except Exception:
        pass
    return [original_segment]


def _replace_message_segments(message: Any, segments: list[Any]) -> bool:
    """原地替换消息段列表。"""
    try:
        message.clear()
        message.extend(segments)
        return True
    except Exception:
        pass

    try:
        while len(message):
            message.pop(0)
        for segment in segments:
            message.append(segment)
        return True
    except Exception:
        return False


def move_non_text_segments_to_end(value: Any) -> bool:
    """清理文本边界空白，并把所有非文本消息段后置。"""
    message = (
        event_message(value)
        if hasattr(value, "get_message") or hasattr(value, "message")
        else value
    )
    if message is None:
        return False

    try:
        segments = list(message)
    except Exception:
        return False
    if not segments:
        return False

    first_text_index = next(
        (index for index, segment in enumerate(segments) if is_text_segment(segment)),
        None,
    )
    if first_text_index is None:
        return False

    has_non_text_segment = any(not is_text_segment(segment) for segment in segments)
    if not has_non_text_segment:
        return False

    text_segments: list[Any] = []
    non_text_segments: list[Any] = []
    changed = False
    first_non_empty_text_seen = False

    for segment in segments:
        if not is_text_segment(segment):
            non_text_segments.append(segment)
            continue

        text = segment_text(segment)
        stripped = text.strip()
        if not stripped:
            changed = True
            continue

        if not first_non_empty_text_seen:
            first_non_empty_text_seen = True
            if stripped != text:
                text_segments.extend(_make_text_segments(message, segment, stripped))
                changed = True
                continue

        text_segments.append(segment)

    reordered = text_segments + non_text_segments
    if reordered != segments:
        changed = True
    return _replace_message_segments(message, reordered) if changed else False


def extract_first_text_match(
    message: Any,
    pattern: Pattern[str],
    *,
    ignored_segment_types: set[str] | None = None,
) -> Match[str] | None:
    """从首个有效文本段中提取正则匹配结果。"""
    ignored = ignored_segment_types or {"image", "reply"}
    for segment in iter_message_segments(message):
        if segment_type(segment) in ignored:
            continue
        text = segment_text(segment).strip()
        if not text:
            continue
        return pattern.match(text)
    return None


def extract_image_sources(message: Any) -> list[str]:
    """从消息对象中提取图片来源。"""
    sources: list[str] = []
    for segment in iter_message_segments(message):
        if segment_type(segment) != "image":
            continue
        source = segment_data(segment).get("url") or segment_data(segment).get("file")
        if isinstance(source, str) and source and not source.startswith("base64://"):
            sources.append(source)
    return sources


def extract_raw_image_sources(message: Any) -> list[str | bytes]:
    """从消息对象中提取图片来源，保留 base64 和字节输入。"""
    sources: list[str | bytes] = []
    for segment in iter_message_segments(message):
        if segment_type(segment) != "image":
            continue
        data = segment_data(segment)
        url = data.get("url")
        if isinstance(url, str) and url.strip():
            sources.append(url.strip())
            continue
        file_value = data.get("file")
        if isinstance(file_value, bytes):
            sources.append(file_value)
        elif isinstance(file_value, str) and file_value.strip():
            sources.append(file_value.strip())
    return sources


def extract_reply_message_id(message: Any) -> int | None:
    """从消息对象中提取回复消息 ID。"""
    for segment in iter_message_segments(message):
        if segment_type(segment) != "reply":
            continue
        try:
            return int(segment_data(segment).get("id"))
        except (TypeError, ValueError):
            return None
    return None


async def get_replied_message(bot: Any, message_id: int) -> Any:
    """通过适配器 API 获取被回复消息。"""
    return await get_message_adapter(bot).get_replied_message(bot, message_id)


async def send_ark_message(bot: Any, event: Any, ark_data: dict[str, Any]) -> Any:
    """发送 QQ ARK 消息。"""
    return await get_message_adapter(bot).send_event(bot, event, {"type": "ark", "data": ark_data})


async def send_text_to_target(bot: Any, target: dict[str, Any], text: str) -> Any:
    """根据保存的目标信息发送文本消息。"""
    return await require_message_adapter(bot).send_text_to_target(bot, target, text)


async def send_message_to_target(bot: Any, target: dict[str, Any], message: Any) -> Any:
    """根据保存的目标信息发送消息。"""
    return await require_message_adapter(bot).send_message_to_target(bot, target, message)


async def send_forward_messages(
    bot: Any, event: Any, messages: list[Any], *, nickname: str = "FxBot"
) -> bool:
    """通过当前适配器发送一组转发消息。"""
    adapter = get_message_adapter(bot)
    if adapter is None:
        return False
    return await adapter.send_forward_messages(bot, event, messages, nickname=nickname)


async def send_forward_texts(
    bot: Any, event: Any, texts: list[str], *, nickname: str = "FxBot"
) -> bool:
    """尝试把多段文本作为 OneBot V11 合并转发发送。"""
    messages = [build_message(bot, build_message_segment(bot, "text", text)) for text in texts]
    return await send_forward_messages(bot, event, messages, nickname=nickname)


def _image_bytes(data: Any) -> bytes:
    """将本地图片输入转换为字节。"""
    import io

    if isinstance(data, bytes):
        return data
    if isinstance(data, Path):
        return data.read_bytes()
    if isinstance(data, str):
        return Path(data).read_bytes()
    buffer = io.BytesIO()
    data.save(buffer, format="PNG")
    return buffer.getvalue()


class _GenericMessageAdapter(MessageAdapter):
    """仅供明确选择的非平台消息场景使用，不参与平台适配器兜底。"""

    def match(self, bot: Any) -> bool:
        return False

    def build_segment(self, bot: Any, seg_type: str, data: Any = None) -> Any:
        if seg_type == "text":
            return str(data)
        raise ValueError(f"不支持的消息段类型: {seg_type} ({adapter_name(bot)})")

    def build_message(self, bot: Any, segments: list[Any]) -> Any:
        return "".join(str(segment) for segment in segments)

    async def send_message_to_target(self, bot: Any, target: dict[str, Any], message: Any) -> Any:
        raise RuntimeError("当前适配器不支持目标消息发送")


__all__ = [
    "MessageAdapter",
    "build_message",
    "build_message_segment",
    "event_message",
    "extract_first_text_match",
    "extract_image_sources",
    "extract_raw_image_sources",
    "extract_message_target",
    "extract_reply_message_id",
    "get_message_adapter",
    "get_replied_message",
    "is_text_segment",
    "move_non_text_segments_to_end",
    "register_message_adapter",
    "send_forward_messages",
    "send_forward_texts",
    "send_message_to_target",
    "send_ark_message",
    "send_text_to_target",
]
