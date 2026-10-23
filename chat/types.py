"""ChatRequest、ChatResponse 和入站消息段类型。"""

from __future__ import annotations

from typing import Any
from dataclasses import field, dataclass


@dataclass
class InboundSegment:
    """入站消息段。"""

    type: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class MentionRef:
    """消息中被 @ 的成员。"""

    user_id: str
    name: str = ""

    def render(self) -> str:
        """渲染成模型可读的形式，昵称可能缺失。"""
        return f"{self.name}({self.user_id})" if self.name else self.user_id


@dataclass
class ChatRequest:
    """AI 对话请求。"""

    session_id: str
    user_id: str
    group_id: str | None
    text: str
    user_name: str = ""
    segments: list[InboundSegment] = field(default_factory=list)
    images: list[str] = field(default_factory=list)
    quoted_text: str = ""
    platform: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    mentions: list[MentionRef] = field(default_factory=list)

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
    epoch: int = 0
