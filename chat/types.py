"""ChatRequest、ChatResponse 和入站消息段类型。"""

from __future__ import annotations

from typing import Any, Literal
from datetime import datetime
from dataclasses import field, dataclass

from ..utils.tz import now, to_naive_utc

TriggerKind = Literal["mention", "persona", "private", "proactive"]


def _utc_now_naive() -> datetime:
    """返回 naive UTC，供请求默认发生时间使用。"""
    return to_naive_utc(now())


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


def is_proactive(request: ChatRequest) -> bool:
    """本轮是否为主动触发。"""
    return request.proactive or request.trigger_kind == "proactive"


def speaker_label(user_id: str, user_name: str) -> str:
    """生成发言人标识，用于区分同一会话里的不同成员。"""
    user_id = str(user_id or "").strip()
    # 昵称是用户可控的，压成单行并限长，避免用换行伪造出第二段消息
    user_name = " ".join(str(user_name or "").split())[:32]
    if not user_name or user_name == user_id:
        return f"用户{user_id}" if user_id else "用户"
    return f"{user_name}({user_id})" if user_id else user_name


@dataclass
class ChatRequest:
    """AI 对话请求。"""

    session_id: str
    user_id: str
    group_id: str | None
    text: str
    turn_id: str
    dedupe_key: str
    trigger_kind: TriggerKind
    proactive: bool = False
    event_id: str | None = None
    message_id: str | None = None
    reply_to_message_id: str | None = None
    occurred_at: datetime = field(default_factory=_utc_now_naive)
    user_name: str = ""
    segments: list[InboundSegment] = field(default_factory=list)
    images: list[str] = field(default_factory=list)
    quoted_text: str = ""
    platform: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    mentions: list[MentionRef] = field(default_factory=list)
    batched: bool = False
    source_message_ids: list[str] = field(default_factory=list)
    source_author_ids: list[str] = field(default_factory=list)
    trigger_seq: int = 0
    # 序号归属者：主动轮与用户消息各记一本，判断「是否还有更新的触发」时按归属者查
    trigger_owner: str = ""
    accepted_epoch: int | None = None
    activity_seq: int = 0

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
