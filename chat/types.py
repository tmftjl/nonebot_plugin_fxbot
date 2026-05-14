"""ChatRequest、ChatResponse 和入站消息段类型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class InboundSegment:
    """入站消息段。"""

    type: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChatRequest:
    """AI 对话请求。"""

    session_id: str
    user_id: str
    group_id: str | None
    text: str
    segments: list[InboundSegment] = field(default_factory=list)
    platform: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_group(self) -> bool:
        """是否为群聊。"""
        return bool(self.group_id)


@dataclass
class ChatResponse:
    """AI 对话响应。"""

    text: str
    raw: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)
