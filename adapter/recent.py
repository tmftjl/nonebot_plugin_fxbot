"""缓存各会话最近收到的消息轻量信息。"""

from __future__ import annotations

from typing import Any
from collections import deque, defaultdict

_RECENT_LIMIT = 21  # 当前事件会先入队，额外一位确保此前 20 条仍可引用
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


def _message_indices(event: Any) -> list[str]:
    """提取当前消息的全部可引用索引，不混入被引用消息索引。"""
    values = [_ext_value(event, "msg_idx"), _value(event, "msg_idx")]
    if not _ext_value(event, "ref_msg_idx"):
        values.extend(_value(element, "msg_idx") for element in (getattr(event, "msg_elements", None) or []))
    return list(dict.fromkeys(value for value in values if value))


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


def _event_images(event: Any) -> list[str]:
    return [
        str(attachment.url)
        for attachment in (getattr(event, "attachments", None) or [])
        if getattr(attachment, "url", None) and str(getattr(attachment, "content_type", "")).startswith("image/")
    ]


def _sender_id(event: Any) -> str:
    author = getattr(event, "author", None)
    return _value(author, "id", "user_openid", "member_openid")


def remember_event(bot: Any, event: Any, message: Any) -> None:
    """记录消息 ID、图片 URL和少量元数据。"""
    key = _key(bot, event)
    message_id = _value(event, "id", "message_id")
    msg_indices = _message_indices(event)
    if not key or not message_id or not msg_indices:
        return
    images = list(dict.fromkeys([*_images(message), *_event_images(event)]))
    item = {"id": message_id, "msg_indices": msg_indices, "sender_id": _sender_id(event), "images": images}
    existing = next((stored for stored in _recent[key] if stored.get("id") == message_id), None)
    if existing is not None:
        existing.update(item)
    else:
        _recent[key].append(item)


def recent_reply(bot: Any, event: Any) -> dict[str, Any] | None:
    """按引用索引精确返回当前会话缓存的原消息。"""
    key = _key(bot, event)
    reply_idx = _reply_idx(event)
    if not key or not reply_idx:
        return None
    return next(
        (item for item in reversed(_recent.get(key, ())) if reply_idx in item.get("msg_indices", ())),
        None,
    )
