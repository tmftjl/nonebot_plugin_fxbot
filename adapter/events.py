"""平台无关的事件字段提取工具。"""

from __future__ import annotations

from typing import Any

from nonebot.adapters import Event


def event_message_type(event: Event) -> str:
    return str(getattr(event, "message_type", "") or "").strip().lower()


def event_user_id(event: Event) -> str:
    get_user_id = getattr(event, "get_user_id", None)
    value = get_user_id() if callable(get_user_id) else None
    if value is not None:
        return str(value)
    for attr in ("user_id", "user_openid", "author"):
        value = getattr(event, attr, None)
        for key in ("user_openid", "member_openid", "id"):
            nested = getattr(value, key, None)
            if nested is not None:
                return str(nested)
        if value is not None:
            return str(value)
    return ""


def _raw_group_id(event: Event) -> str | None:
    for attr in ("group_id", "group_openid", "channel_id", "guild_id"):
        value = getattr(event, attr, None)
        if value is not None:
            return str(value)
    return None


def event_is_group(event: Event) -> bool:
    return event_message_type(event) == "group" or (
        not event_message_type(event) and _raw_group_id(event) is not None
    )


def event_is_private(event: Event) -> bool:
    return event_message_type(event) == "private" or (
        not event_message_type(event)
        and bool(event_user_id(event))
        and _raw_group_id(event) is None
    )


def event_is_tome(event: Event) -> bool:
    is_tome = getattr(event, "is_tome", None)
    return bool(is_tome()) if callable(is_tome) else bool(getattr(event, "to_me", False))


def event_group_id(event: Event) -> str | None:
    return _raw_group_id(event) if event_is_group(event) else None


def event_user_name(event: Event, user_id: str = "") -> str:
    for value, keys in (
        (getattr(event, "sender", None), ("card", "nickname", "nick")),
        (
            getattr(event, "author", None),
            ("username", "nickname", "nick", "display_name"),
        ),
    ):
        for key in keys:
            name = value.get(key) if isinstance(value, dict) else getattr(value, key, None)
            if name:
                return str(name)
    return str(user_id or "")


def extract_message_target(event: Any) -> dict[str, Any]:
    get_session_id = getattr(event, "get_session_id", None)
    target = {
        "user_id": getattr(event, "user_id", None),
        "session_id": get_session_id() if callable(get_session_id) else None,
    }
    author = getattr(event, "author", None)
    user_openid = getattr(event, "user_openid", None) or getattr(author, "user_openid", None)
    if user_openid is not None:
        target["user_openid"] = user_openid
    if event_is_group(event):
        for field in ("group_id", "group_openid", "channel_id", "guild_id"):
            value = getattr(event, field, None)
            if value is not None:
                target[field] = value
    return target
