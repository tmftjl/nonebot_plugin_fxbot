"""聊天消息、轮次与会话状态的持久化模型。"""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Field
from sqlalchemy import Index, UniqueConstraint, text

from ...db import BaseIDModel
from ...utils.tz import now, to_naive_utc


def utc_now_naive() -> datetime:
    """返回当前时刻的 naive UTC。"""
    return to_naive_utc(now())


class ChatMessage(BaseIDModel, table=True):
    """原始消息与撤回标记。"""

    __tablename__ = "chat_message"
    __table_args__ = (
        UniqueConstraint("tenant", "dedupe_key"),
        Index("ix_chat_message_tenant_id", "tenant", "id"),
        {"sqlite_autoincrement": True},
    )

    tenant: str = Field(nullable=False)
    kind: str = Field(default="user", nullable=False)
    platform: str = Field(default="", nullable=False)
    platform_event_id: str | None = Field(default=None, nullable=True)
    platform_message_id: str | None = Field(default=None, nullable=True)
    reply_to_message_id: str | None = Field(default=None, nullable=True)
    sender_id: str = Field(default="", nullable=False)
    sender_name: str = Field(default="", nullable=False)
    content: str = Field(default="", nullable=False)
    payload: str = Field(default="{}", nullable=False)
    occurred_at: datetime = Field(default_factory=utc_now_naive, nullable=False)
    ingested_at: datetime = Field(default_factory=utc_now_naive, nullable=False)
    source: str = Field(default="live", nullable=False)
    dedupe_key: str = Field(nullable=False)
    deleted: bool = Field(default=False, nullable=False)
    deleted_at: datetime | None = Field(default=None, nullable=True)
    delete_operator: str = Field(default="", nullable=False)
    recall_note: str = Field(default="", nullable=False)


class ChatTurn(BaseIDModel, table=True):
    """一轮生成的执行与投递状态。"""

    __tablename__ = "chat_turn"
    __table_args__ = (
        UniqueConstraint("tenant", "turn_key"),
        Index("ix_chat_turn_tenant_created", "tenant", "created_at"),
    )

    tenant: str = Field(nullable=False)
    turn_key: str = Field(nullable=False)
    generation: int = Field(default=0, nullable=False)
    trigger_message_ids: str = Field(default="[]", nullable=False)
    author_ids: str = Field(default="[]", nullable=False)
    trigger_kind: str = Field(default="mention", nullable=False)
    request_digest: str = Field(default="", nullable=False)
    status: str = Field(default="received", nullable=False)
    generated_text: str = Field(default="", nullable=False)
    provider: str = Field(default="", nullable=False)
    error: str = Field(default="", nullable=False)
    started_at: datetime | None = Field(default=None, nullable=True)
    finished_at: datetime | None = Field(default=None, nullable=True)
    created_at: datetime = Field(default_factory=utc_now_naive, nullable=False)


class ChatTurnItem(BaseIDModel, table=True):
    """轮内有序内容与发送结果。"""

    __tablename__ = "chat_turn_item"
    __table_args__ = (
        UniqueConstraint("tenant", "turn_id", "item_seq"),
        Index("ix_chat_turn_item_tenant_turn", "tenant", "turn_id"),
        Index(
            "ux_chat_turn_item_tool",
            "tenant",
            "turn_id",
            "tool_call_id",
            unique=True,
            sqlite_where=text("tool_call_id != ''"),
        ),
    )

    tenant: str = Field(nullable=False)
    turn_id: int = Field(default=0, nullable=False)
    item_seq: int = Field(default=0, nullable=False)
    role: str = Field(nullable=False)
    content: str = Field(default="", nullable=False)
    payload: str = Field(default="{}", nullable=False)
    source_message_id: int | None = Field(default=None, nullable=True)
    tool_call_id: str = Field(default="", nullable=False)
    part_index: int = Field(default=0, nullable=False)
    visible: bool = Field(default=False, nullable=False)
    send_status: str = Field(default="", nullable=False)
    platform_message_id: str | None = Field(default=None, nullable=True)
    created_at: datetime = Field(default_factory=utc_now_naive, nullable=False)


class ChatSessionState(BaseIDModel, table=True):
    """会话代次与记忆开关的持久状态。"""

    __tablename__ = "chat_session_state"
    __table_args__ = (UniqueConstraint("tenant"),)

    tenant: str = Field(nullable=False)
    generation: int = Field(default=0, nullable=False)
    cleared_at: datetime | None = Field(default=None, nullable=True)
    last_active_at: datetime | None = Field(default=None, nullable=True)
    learn_enabled: bool | None = Field(default=None, nullable=True)
    degraded_reason: str = Field(default="", nullable=False)
    updated_at: datetime = Field(default_factory=utc_now_naive, nullable=False)
