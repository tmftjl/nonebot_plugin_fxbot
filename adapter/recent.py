"""缓存各会话最近收到的消息轻量信息。"""

from __future__ import annotations

from typing import Any
from collections import deque, defaultdict

_RECENT_LIMIT = 20
_recent: dict[str, deque[dict[str, Any]]] = defaultdict(lambda: deque(maxlen=_RECENT_LIMIT))


def _value(obj: Any, *names: str) -> str:
    for name in names:
        value = getattr(obj, name, None)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _key(bot: Any, event: Any) -> str:
    bot_id = _value(bot, "self_id", "id") or "unknown-bot"
    scene = _value(event, "group_id", "group_openid", "channel_id", "guild_id")
    if scene:
        return f"{bot_id}:scene:{scene}"
    user = _value(event, "user_id", "user_openid", "member_openid")
    if not user:
        user = _value(getattr(event, "author", None), "id", "user_openid", "member_openid")
    return f"{bot_id}:user:{user}" if user else ""


def _ext_value(event: Any, name: str) -> str:
    scene = getattr(event, "message_scene", None)
    prefix = f"{name}="
    for value in getattr(scene, "ext", None) or []:
        if isinstance(value, str) and value.startswith(prefix):
            return value.removeprefix(prefix)
    return ""


def _message_idx(event: Any) -> str:
    return _value(event, "msg_idx") or _ext_value(event, "msg_idx")


def _reply_idx(event: Any) -> str:
    return _value(getattr(event, "reply", None), "msg_idx") or _ext_value(event, "ref_msg_idx")


def _images(message: Any) -> list[str]:
    result: list[str] = []
    for segment in list(message or []):
        if getattr(segment, "type", "") != "image":
            continue
        data = getattr(segment, "data", {}) or {}
        source = data.get("url") or data.get("file") or data.get("file_id")
        if isinstance(source, str) and source and not source.startswith("base64://"):
            result.append(source)
    return result


def remember_event(bot: Any, event: Any, message: Any) -> None:
    """记录消息 ID、图片 URL和少量元数据。"""
    key = _key(bot, event)
    message_id = _value(event, "id", "message_id")
    msg_idx = _message_idx(event)
    if not key or not message_id or not msg_idx:
        return
    _recent[key].append({"id": message_id, "msg_idx": msg_idx, "images": _images(message)})


def recent_message_ids(bot: Any, event: Any) -> list[str]:
    """按引用索引返回当前会话缓存的原消息 ID。"""
    key = _key(bot, event)
    reply_idx = _reply_idx(event)
    if not key or not reply_idx:
        return []
    return [item["id"] for item in reversed(_recent.get(key, ())) if item.get("msg_idx") == reply_idx]


def recent_image_sources(bot: Any, event: Any) -> list[str]:
    """按引用索引返回当前会话缓存的原消息图片 URL。"""
    key = _key(bot, event)
    reply_idx = _reply_idx(event)
    if not key or not reply_idx:
        return []
    for item in reversed(_recent.get(key, ())):
        if item.get("msg_idx") == reply_idx:
            return list(item["images"])
    return []
