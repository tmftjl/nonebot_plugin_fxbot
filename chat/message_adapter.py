"""将 NoneBot MessageEvent 适配为 ChatRequest。"""

from __future__ import annotations

from typing import Any

from .types import ChatRequest, InboundSegment
from ..adapter import normalize_id, event_user_id, event_group_id, event_plain_text


def adapt_message_event(event: Any) -> ChatRequest:
    """适配 NoneBot 消息事件。"""
    user_id = normalize_id(event_user_id(event)) or ""
    group_id = normalize_id(event_group_id(event))
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
        text=event_plain_text(event),
        segments=segments,
        platform=event.get_type() if hasattr(event, "get_type") else "",
        metadata={"event": event},
    )
