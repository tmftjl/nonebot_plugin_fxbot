"""消息适配器注册与通用入口。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from re import Match, Pattern
from typing import Any

from nonebot.adapters import Bot, Event

from .support import adapter_name, extract_message_target, is_onebot_v11, is_qq_official

MENTION_SEGMENT_TYPES = {"at", "mention", "mention_user"}
MENTION_TARGET_KEYS = ("qq", "user_id", "id", "target")


class MessageAdapter(ABC):
    """平台消息适配器。"""

    @abstractmethod
    def match(self, bot: Bot) -> bool:
        """判断当前适配器是否支持该 Bot。"""

    @abstractmethod
    def build_segment(self, bot: Bot, seg_type: str, data: Any = None) -> Any:
        """构造平台消息段。"""

    @abstractmethod
    def build_message(self, bot: Bot, segments: list[Any]) -> Any:
        """构造平台消息对象。"""

    @abstractmethod
    async def send_message_to_target(self, bot: Bot, target: dict[str, Any], message: Any) -> Any:
        """向持久化目标发送消息。"""

    async def send_text_to_target(self, bot: Bot, target: dict[str, Any], text: str) -> Any:
        """向持久化目标发送文本消息。"""
        message = self.build_message(bot, [self.build_segment(bot, "text", text)])
        return await self.send_message_to_target(bot, target, message)

    async def send_forward_messages(self, bot: Bot, event: Event, messages: list[Any], *, nickname: str = "FxBot") -> bool:
        """发送一组转发消息。"""
        return False

    def extract_image_sources(self, message: Any) -> list[str]:
        """从消息对象中提取图片来源。"""
        try:
            iterable = list(message)
        except Exception:
            iterable = []
        sources: list[str] = []
        for segment in iterable:
            if getattr(segment, "type", "") != "image":
                continue
            data = getattr(segment, "data", {}) or {}
            source = data.get("url") or data.get("file")
            if isinstance(source, str) and source and not source.startswith("base64://"):
                sources.append(source)
        return sources

    def extract_reply_message_id(self, message: Any) -> int | None:
        """从消息对象中提取回复消息 ID。"""
        try:
            iterable = list(message)
        except Exception:
            iterable = []
        for segment in iterable:
            if getattr(segment, "type", "") != "reply":
                continue
            value = (getattr(segment, "data", {}) or {}).get("id")
            if value is None:
                continue
            try:
                return int(value)
            except Exception:
                return None
        return None

    async def get_replied_message(self, bot: Bot, message_id: int) -> Any:
        """通过适配器 API 获取被回复消息。"""
        if hasattr(bot, "get_msg"):
            result = await bot.get_msg(message_id=message_id)
        else:
            result = await bot.call_api("get_msg", message_id=message_id)
        if isinstance(result, dict):
            return result.get("message")
        return None


_adapters: list[MessageAdapter] = []


def register_message_adapter(adapter: MessageAdapter | type[MessageAdapter]) -> MessageAdapter | type[MessageAdapter]:
    """注册消息适配器。"""
    _adapters.append(adapter() if isinstance(adapter, type) else adapter)
    return adapter


def get_message_adapter(bot: Bot) -> MessageAdapter | None:
    """获取当前 Bot 对应的消息适配器。"""
    for adapter in _adapters:
        if adapter.match(bot):
            return adapter
    return None


def require_message_adapter(bot: Bot) -> MessageAdapter:
    """获取消息适配器，不支持时抛出明确错误。"""
    adapter = get_message_adapter(bot)
    if adapter:
        return adapter
    raise ValueError(f"未支持的适配器：{adapter_name(bot)}")


def get_onebot_v11_message_segment_class():
    """获取 OneBot V11 MessageSegment 类。"""
    from .onebot11 import OneBotV11MessageAdapter

    return OneBotV11MessageAdapter.message_segment_class()


def build_message_segment(bot: Bot, seg_type: str, data: Any = None) -> Any:
    """根据适配器构造消息段。"""
    return require_message_adapter(bot).build_segment(bot, seg_type, data)


def build_message(bot: Bot, *segments: Any) -> Any:
    """根据适配器构造消息对象。"""
    filtered = [segment for segment in segments if segment is not None]
    adapter = get_message_adapter(bot)
    if adapter:
        return adapter.build_message(bot, filtered)
    return "".join(str(segment) for segment in filtered)


def event_message(event: Any) -> Any:
    """提取事件消息对象。"""
    if hasattr(event, "get_message"):
        try:
            return event.get_message()
        except Exception:
            pass
    return getattr(event, "message", None)


def iter_message_segments(message: Any) -> list[Any]:
    """遍历消息段。"""
    if message is None:
        return []
    try:
        return list(message)
    except Exception:
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


def is_mention_segment(segment: Any) -> bool:
    """判断消息段是否为用户 @。"""
    return segment_type(segment) in MENTION_SEGMENT_TYPES


def mention_target(segment: Any) -> str | None:
    """提取 @ 消息段中的用户 ID。"""
    if not is_mention_segment(segment):
        return None
    data = segment_data(segment)
    for key in MENTION_TARGET_KEYS:
        value = data.get(key)
        text = str(value or "").strip()
        if text and text != "all":
            return text
    return None


def mention_targets(message: Any, *, ignored_targets: set[str] | None = None) -> list[str]:
    """提取消息中的所有 @ 用户 ID。"""
    ignored = {str(item) for item in ignored_targets or set()}
    targets: list[str] = []
    for segment in iter_message_segments(message):
        target = mention_target(segment)
        if target and target not in ignored:
            targets.append(target)
    return targets


def first_mention_target(message: Any, *, ignored_targets: set[str] | None = None) -> str | None:
    """提取消息中的第一个 @ 用户 ID。"""
    targets = mention_targets(message, ignored_targets=ignored_targets)
    return targets[0] if targets else None


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
    """把所有非文本消息段后置，保留文本段和非文本段各自的相对顺序。"""
    message = event_message(value) if hasattr(value, "get_message") or hasattr(value, "message") else value
    if message is None:
        return False

    try:
        segments = list(message)
    except Exception:
        return False
    if not segments:
        return False

    first_text_index = next((index for index, segment in enumerate(segments) if is_text_segment(segment)), None)
    if first_text_index is None:
        return False

    non_text_before_first_text = any(not is_text_segment(segment) for segment in segments[:first_text_index])
    text_segments: list[Any] = []
    non_text_segments: list[Any] = []
    changed = False
    first_non_empty_text_seen = False

    for segment in segments:
        if not is_text_segment(segment):
            non_text_segments.append(segment)
            continue

        text = segment_text(segment)
        if non_text_before_first_text and not first_non_empty_text_seen:
            if not text.strip():
                changed = True
                continue
            first_non_empty_text_seen = True
            stripped = text.lstrip()
            if stripped != text:
                text_segments.extend(_make_text_segments(message, segment, stripped))
                changed = True
                continue

        if text.strip():
            first_non_empty_text_seen = True
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
    ignored = ignored_segment_types or {"image", "reply", *MENTION_SEGMENT_TYPES}
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
    if isinstance(message, str) and is_onebot_v11_string_available():
        from nonebot.adapters.onebot.v11 import Message

        try:
            message = Message(message)
        except Exception:
            pass
    return _generic_adapter().extract_image_sources(message)


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
    if isinstance(message, str) and is_onebot_v11_string_available():
        from nonebot.adapters.onebot.v11 import Message

        try:
            message = Message(message)
        except Exception:
            pass
    return _generic_adapter().extract_reply_message_id(message)


async def get_replied_message(bot: Bot, message_id: int) -> Any:
    """通过适配器 API 获取被回复消息。"""
    adapter = get_message_adapter(bot) or _generic_adapter()
    return await adapter.get_replied_message(bot, message_id)


async def send_ark_message(bot: Bot, event: Any, ark_data: dict[str, Any]) -> Any:
    """发送 QQ ARK 消息。"""
    if not is_qq_official(bot):
        raise RuntimeError("ARK 消息仅支持 QQ 官方适配器")
    return await bot.send(event, message={"type": "ark", "data": ark_data})


async def send_text_to_target(bot: Bot, target: dict[str, Any], text: str) -> Any:
    """根据保存的目标信息发送文本消息。"""
    return await require_message_adapter(bot).send_text_to_target(bot, target, text)


async def send_message_to_target(bot: Bot, target: dict[str, Any], message: Any) -> Any:
    """根据保存的目标信息发送消息。"""
    return await require_message_adapter(bot).send_message_to_target(bot, target, message)


async def send_forward_messages(bot: Bot, event: Event, messages: list[Any], *, nickname: str = "FxBot") -> bool:
    """通过当前适配器发送一组转发消息。"""
    adapter = get_message_adapter(bot)
    if adapter is None:
        return False
    return await adapter.send_forward_messages(bot, event, messages, nickname=nickname)


async def send_forward_texts(bot: Bot, event: Event, texts: list[str], *, nickname: str = "FxBot") -> bool:
    """尝试把多段文本作为 OneBot V11 合并转发发送。"""
    if not is_onebot_v11(bot):
        return False
    messages = [
        build_message(bot, build_message_segment(bot, "text", text))
        for text in texts
    ]
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


def is_onebot_v11_string_available() -> bool:
    """判断能否把 CQ 字符串解析成 OneBot V11 Message。"""
    import importlib.util

    return importlib.util.find_spec("nonebot.adapters.onebot.v11") is not None


class _GenericMessageAdapter(MessageAdapter):
    """只提供通用解析能力的兜底适配器。"""

    def match(self, bot: Bot) -> bool:
        return False

    def build_segment(self, bot: Bot, seg_type: str, data: Any = None) -> Any:
        if seg_type == "text":
            return str(data)
        raise ValueError(f"未支持的适配器：{adapter_name(bot)}")

    def build_message(self, bot: Bot, segments: list[Any]) -> Any:
        return "".join(str(segment) for segment in segments)

    async def send_message_to_target(self, bot: Bot, target: dict[str, Any], message: Any) -> Any:
        raise RuntimeError("无法识别消息目标")


_generic = _GenericMessageAdapter()


def _generic_adapter() -> MessageAdapter:
    return _generic


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
    "first_mention_target",
    "get_message_adapter",
    "get_onebot_v11_message_segment_class",
    "get_replied_message",
    "is_mention_segment",
    "is_onebot_v11",
    "is_qq_official",
    "is_text_segment",
    "mention_target",
    "mention_targets",
    "move_non_text_segments_to_end",
    "register_message_adapter",
    "send_forward_messages",
    "send_forward_texts",
    "send_message_to_target",
    "send_ark_message",
    "send_text_to_target",
]
