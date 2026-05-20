"""适配器兼容辅助函数。"""

from __future__ import annotations

import base64
import importlib.util
import io
from pathlib import Path
from typing import Any

from nonebot.adapters import Bot


def has_onebot_v11() -> bool:
    """判断当前环境是否安装 OneBot V11 适配器。"""
    return importlib.util.find_spec("nonebot.adapters.onebot.v11") is not None


def has_qq_official() -> bool:
    """判断当前环境是否安装 QQ 官方适配器。"""
    return importlib.util.find_spec("nonebot.adapters.qq") is not None


def _adapter_module(bot: Bot) -> str:
    """提取适配器类模块名。"""
    adapter = getattr(bot, "adapter", None)
    return str(getattr(getattr(adapter, "__class__", None), "__module__", "") or "")


def is_onebot_v11(bot: Bot) -> bool:
    """判断 Bot 是否来自 OneBot V11 适配器。"""
    return has_onebot_v11() and _adapter_module(bot).startswith("nonebot.adapters.onebot.v11")


def is_qq_official(bot: Bot) -> bool:
    """判断 Bot 是否来自 QQ 官方适配器。"""
    return has_qq_official() and _adapter_module(bot).startswith("nonebot.adapters.qq")


def get_onebot_v11_message_segment_class():
    """获取 OneBot V11 MessageSegment 类。"""
    if not has_onebot_v11():
        return None
    try:
        from nonebot.adapters.onebot.v11 import MessageSegment
    except Exception:
        return None
    return MessageSegment


def _image_bytes(data: Any) -> bytes:
    """将本地图片输入转换为字节。"""
    if isinstance(data, bytes):
        return data
    if isinstance(data, Path):
        return data.read_bytes()
    if isinstance(data, str):
        return Path(data).read_bytes()
    buffer = io.BytesIO()
    data.save(buffer, format="PNG")
    return buffer.getvalue()


def build_message_segment(bot: Bot, seg_type: str, data: Any = None):
    """根据适配器构造消息段。"""
    if is_onebot_v11(bot):
        from nonebot.adapters.onebot.v11 import MessageSegment

        if seg_type == "text":
            return MessageSegment.text(str(data))
        if seg_type == "at":
            return MessageSegment.at(str(data))
        if seg_type == "image":
            if isinstance(data, str) and (data.startswith(("http://", "https://", "base64://"))):
                return MessageSegment.image(data)
            return MessageSegment.image(_image_bytes(data))
        if seg_type == "record":
            return MessageSegment.record(_image_bytes(data) if isinstance(data, Path) else data)
        raise ValueError(f"不支持的消息段类型: {seg_type}")

    if is_qq_official(bot):
        from nonebot.adapters.qq import MessageSegment

        if seg_type == "text":
            return MessageSegment.text(str(data))
        if seg_type == "at":
            return MessageSegment.mention_user(str(data))
        if seg_type == "image":
            if isinstance(data, str) and data.startswith(("http://", "https://")):
                return MessageSegment.image(data)
            if isinstance(data, str) and data.startswith("base64://"):
                return MessageSegment.file_image(base64.b64decode(data[9:]))
            return MessageSegment.file_image(_image_bytes(data))
        if seg_type == "record":
            if isinstance(data, str) and data.startswith(("http://", "https://")):
                return MessageSegment.audio(data)
            if isinstance(data, str) and data.startswith("base64://"):
                return MessageSegment.file_audio(base64.b64decode(data[9:]))
            return MessageSegment.file_audio(_image_bytes(data))
        raise ValueError(f"不支持的消息段类型: {seg_type}")

    if seg_type == "text":
        return str(data)
    raise ValueError("未支持的适配器：仅支持 OneBot V11 / QQ 官方")


def build_message(bot: Bot, *segments: Any):
    """根据适配器构造消息对象。"""
    filtered = [segment for segment in segments if segment is not None]

    if is_onebot_v11(bot):
        from nonebot.adapters.onebot.v11 import Message

        return Message(filtered)

    if is_qq_official(bot):
        from nonebot.adapters.qq import Message

        return Message(filtered)

    return "".join(str(segment) for segment in filtered)


def extract_image_sources(message: Any) -> list[str]:
    """从消息对象中提取图片来源。"""
    if isinstance(message, str) and has_onebot_v11():
        try:
            from nonebot.adapters.onebot.v11 import Message

            message = Message(message)
        except Exception:
            pass
    sources: list[str] = []
    try:
        iterable = list(message)
    except Exception:
        iterable = []
    for segment in iterable:
        if getattr(segment, "type", "") != "image":
            continue
        data = getattr(segment, "data", {}) or {}
        source = data.get("url") or data.get("file")
        if isinstance(source, str) and source and not source.startswith("base64://"):
            sources.append(source)
    return sources


def extract_reply_message_id(message: Any) -> int | None:
    """从消息对象中提取回复消息 ID。"""
    if isinstance(message, str) and has_onebot_v11():
        try:
            from nonebot.adapters.onebot.v11 import Message

            message = Message(message)
        except Exception:
            pass
    try:
        iterable = list(message)
    except Exception:
        iterable = []
    for segment in iterable:
        if getattr(segment, "type", "") != "reply":
            continue
        data = getattr(segment, "data", {}) or {}
        value = data.get("id")
        if value is not None:
            try:
                return int(value)
            except Exception:
                return None
    return None


async def get_replied_message(bot: Bot, message_id: int) -> Any:
    """通过适配器 API 获取被回复消息。"""
    if hasattr(bot, "get_msg"):
        result = await bot.get_msg(message_id=message_id)
    else:
        result = await bot.call_api("get_msg", message_id=message_id)
    if isinstance(result, dict):
        return result.get("message")
    return None


async def send_ark_message(bot: Bot, event: Any, ark_data: dict[str, Any]) -> Any:
    """发送 QQ ARK 消息。"""
    if not is_qq_official(bot):
        raise RuntimeError("ARK 消息仅支持 QQ 官方适配器")
    return await bot.send(event, message={"type": "ark", "data": ark_data})
