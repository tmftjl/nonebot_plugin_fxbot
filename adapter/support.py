"""适配器识别与通用事件字段提取。"""

from __future__ import annotations

import importlib.util
from typing import Any

from nonebot.adapters import Bot, Event


def has_onebot_v11() -> bool:
    """判断当前环境是否安装 OneBot V11 适配器。"""
    return importlib.util.find_spec("nonebot.adapters.onebot.v11") is not None


def has_qq_official() -> bool:
    """判断当前环境是否安装 QQ 官方适配器。"""
    return importlib.util.find_spec("nonebot.adapters.qq") is not None


def adapter_name(bot: Bot) -> str:
    """提取适配器名称。"""
    try:
        return str(bot.adapter.get_name())
    except Exception:
        return str(getattr(bot, "type", "") or "Unknown")


def event_message_type(event: Event) -> str:
    """提取消息事件类型。"""
    return str(getattr(event, "message_type", "") or "").strip().lower()


def _raw_group_id(event: Event) -> str | None:
    """不判断会话类型，直接提取事件携带的群 ID。"""
    for attr in ("group_id", "group_openid"):
        value = getattr(event, attr, None)
        if value is not None:
            return str(value)
    if hasattr(event, "get_group_id"):
        try:
            return str(event.get_group_id())
        except Exception:
            pass
    return None


def _adapter_module(bot: Bot) -> str:
    """提取适配器类模块名。"""
    adapter = getattr(bot, "adapter", None)
    return str(getattr(getattr(adapter, "__class__", None), "__module__", "") or "")


def is_onebot_v11(bot: Bot) -> bool:
    """判断 Bot 是否来自 OneBot V11 适配器。"""
    return has_onebot_v11() and (adapter_name(bot) == "OneBot V11" or _adapter_module(bot).startswith("nonebot.adapters.onebot.v11"))


def is_qq_official(bot: Bot) -> bool:
    """判断 Bot 是否来自 QQ 官方适配器。"""
    return has_qq_official() and (adapter_name(bot) == "QQ" or _adapter_module(bot).startswith("nonebot.adapters.qq"))


def event_user_id(event: Event) -> str:
    """提取事件用户 ID。"""
    if hasattr(event, "get_user_id"):
        try:
            return str(event.get_user_id())
        except Exception:
            pass
    for attr in ("user_id", "user_openid", "author"):
        value = getattr(event, attr, None)
        if hasattr(value, "user_openid"):
            return str(value.user_openid)
        if hasattr(value, "member_openid"):
            return str(value.member_openid)
        if hasattr(value, "id"):
            return str(value.id)
        if value is not None:
            return str(value)
    return ""


def event_is_group(event: Event) -> bool:
    """判断当前事件是否代表群聊会话。"""
    message_type = event_message_type(event)
    if message_type:
        return message_type == "group"
    return _raw_group_id(event) is not None


def event_is_private(event: Event) -> bool:
    """判断当前事件是否代表私聊会话。"""
    message_type = event_message_type(event)
    if message_type:
        return message_type == "private"
    return bool(event_user_id(event)) and _raw_group_id(event) is None


def event_is_tome(event: Event) -> bool:
    """判断消息是否指向当前 Bot。"""
    if hasattr(event, "is_tome"):
        try:
            return bool(event.is_tome())
        except Exception:
            pass
    return bool(getattr(event, "to_me", False))


def event_group_id(event: Event) -> str | None:
    """提取事件群 ID。"""
    return _raw_group_id(event) if event_is_group(event) else None


def extract_message_target(event: Any) -> dict[str, Any]:
    """提取可持久化的消息目标信息。"""
    target: dict[str, Any] = {
        "user_id": getattr(event, "user_id", None),
        "session_id": event.get_session_id() if hasattr(event, "get_session_id") else None,
    }
    if event_is_group(event):
        for field in ("group_id", "group_openid", "channel_id", "guild_id"):
            value = getattr(event, field, None)
            if value is not None:
                target[field] = value
    return target


def qq_avatar(user_id: str) -> str:
    """生成 QQ 头像地址。"""
    return f"http://q1.qlogo.cn/g?b=qq&nk={user_id}&s=640"


def official_qq_avatar(bot: Bot, user_id: str) -> str:
    """生成 QQ 官方适配器头像地址。"""
    bot_info = getattr(bot, "bot_info", None)
    app_id = getattr(bot_info, "id", "") or getattr(bot, "self_id", "")
    return f"https://q.qlogo.cn/qqapp/{app_id}/{user_id}/100"
