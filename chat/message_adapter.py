"""将 NoneBot MessageEvent 适配为 ChatRequest。"""

from __future__ import annotations

from typing import Any

from .types import ChatRequest, InboundSegment


def _normalize_id(value: Any) -> str | None:
    """标准化 ID。"""
    if value is None:
        return None
    text = str(value).strip()
    return text if text and text != "0" else None


def _uid(event: Any) -> str:
    """提取用户 ID。"""
    if hasattr(event, "get_user_id"):
        try:
            return _normalize_id(event.get_user_id()) or ""
        except Exception:
            pass
    return _normalize_id(getattr(event, "user_id", None)) or ""


def _gid(event: Any) -> str | None:
    """提取群 ID。"""
    if hasattr(event, "get_group_id"):
        try:
            return _normalize_id(event.get_group_id())
        except Exception:
            pass
    return _normalize_id(getattr(event, "group_id", None))


def _plain_text(event: Any) -> str:
    """提取纯文本。"""
    if hasattr(event, "get_plaintext"):
        try:
            return str(event.get_plaintext()).strip()
        except Exception:
            pass
    try:
        return str(event.get_message()).strip()
    except Exception:
        return ""


def adapt_message_event(event: Any) -> ChatRequest:
    """适配 NoneBot 消息事件。"""
    user_id = _uid(event)
    group_id = _gid(event)
    session_id = f"group:{group_id}" if group_id else f"private:{user_id}"
    segments: list[InboundSegment] = []
    try:
        for seg in event.get_message():
            segments.append(InboundSegment(type=str(seg.type), data=dict(seg.data)))
    except Exception:
        pass
    return ChatRequest(
        session_id=session_id,
        user_id=user_id,
        group_id=group_id,
        text=_plain_text(event),
        segments=segments,
        platform=event.get_type() if hasattr(event, "get_type") else "",
        metadata={"event": event},
    )
