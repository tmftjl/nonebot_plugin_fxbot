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
    scene = _value(event, "group_openid", "channel_id", "guild_id")
    if scene:
        return f"{bot_id}:scene:{scene}"
    user = _value(event, "user_openid", "member_openid")
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


def _images(message: Any) -> list[str | bytes]:
    result: list[str | bytes] = []
    for segment in list(message or []):
        segment_type = getattr(segment, "type", "")
        data = getattr(segment, "data", {}) or {}
        if segment_type == "image":
            source = data.get("url") or data.get("file") or data.get("file_id")
            if isinstance(source, str) and source and not source.startswith("base64://"):
                result.append(source)
        elif segment_type == "file_image" and isinstance(data.get("content"), (bytes, bytearray)):
            result.append(bytes(data["content"]))
    return result


def _texts(message: Any) -> list[str]:
    text = "".join(
        str((getattr(segment, "data", {}) or {}).get("text") or "")
        for segment in list(message or [])
        if getattr(segment, "type", "") == "text"
    ).strip()
    return [text] if text else []


def _event_images(event: Any) -> list[str]:
    return [
        str(attachment.url)
        for attachment in (getattr(event, "attachments", None) or [])
        if getattr(attachment, "url", None) and str(getattr(attachment, "content_type", "")).startswith("image/")
    ]


def _sender_id(event: Any) -> str:
    author = getattr(event, "author", None)
    return _value(author, "id", "user_openid", "member_openid")


def _sender_name(event: Any) -> str:
    return _value(getattr(event, "author", None), "username", "nickname", "user_name", "name")


def remember_event(bot: Any, event: Any, message: Any) -> None:
    """记录消息 ID、图片 URL和少量元数据。"""
    key = _key(bot, event)
    message_id = _value(event, "id", "message_id")
    msg_indices = _message_indices(event)
    if not key or not message_id or not msg_indices:
        return
    images = list(dict.fromkeys([*_images(message), *_event_images(event)]))
    item = {
        "id": message_id,
        "msg_indices": msg_indices,
        "sender_id": _sender_id(event),
        "sender_name": _sender_name(event),
        "images": images,
        "texts": _texts(message),
    }
    existing = next((stored for stored in _recent[key] if stored.get("id") == message_id), None)
    if existing is not None:
        existing.update(item)
    else:
        _recent[key].append(item)


def remember_sent(
    bot: Any,
    group_openid: str,
    message: Any,
    message_id: str | None,
    ref_idx: str | None,
) -> None:
    """记录 bot 自己发出的群消息：QQ 不回推自己的消息，只能靠发送响应里的引用索引。"""
    bot_id = _value(bot, "self_id", "id")
    if not bot_id or not group_openid or not ref_idx:
        return
    key = f"{bot_id}:scene:{group_openid}"
    item = {
        "id": message_id,
        "msg_indices": [ref_idx],
        "sender_id": bot_id,
        "images": list(dict.fromkeys(_images(message))),
        "texts": _texts(message),
    }
    existing = next((stored for stored in _recent[key] if ref_idx in stored.get("msg_indices", ())), None)
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
