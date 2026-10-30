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


class ChatRecallTombstone(BaseIDModel, table=True):
    """保存先于原始消息落库的撤回通知，落库时消费。"""

    __tablename__ = "chat_recall_tombstone"
    __table_args__ = (UniqueConstraint("tenant", "platform_message_id"),)

    tenant: str = Field(nullable=False)
    platform_message_id: str = Field(nullable=False)
    deleted_at: datetime = Field(default_factory=utc_now_naive, nullable=False)
    delete_operator: str = Field(default="", nullable=False)


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
    """会话代次与学习开关的持久状态。"""

    __tablename__ = "chat_session_state"
    __table_args__ = (UniqueConstraint("tenant"),)

    tenant: str = Field(nullable=False)
    generation: int = Field(default=0, nullable=False)
    cleared_at: datetime | None = Field(default=None, nullable=True)
    last_active_at: datetime | None = Field(default=None, nullable=True)
    learn_enabled: bool | None = Field(default=None, nullable=True)
    proactive_enabled: bool | None = Field(default=None, nullable=True)
    updated_at: datetime = Field(default_factory=utc_now_naive, nullable=False)


class ChatJobCursor(BaseIDModel, table=True):
    """每个租户、每种学习任务的独立游标。"""

    __tablename__ = "chat_job_cursor"
    __table_args__ = (UniqueConstraint("tenant", "job"),)

    tenant: str = Field(nullable=False)
    job: str = Field(default="memory", nullable=False)
    direction: str = Field(default="forward", nullable=False)
    cursor_seq: int = Field(default=0, nullable=False)
    stop_seq: int = Field(default=0, nullable=False)
    fail_count: int = Field(default=0, nullable=False)
    next_retry_at: datetime | None = Field(default=None, nullable=True)
    last_error: str = Field(default="", nullable=False)
    updated_at: datetime = Field(default_factory=utc_now_naive, nullable=False)


class ChatStyle(BaseIDModel, table=True):
    """群说话风格；人工覆盖优先于自动摘要。"""

    __tablename__ = "chat_style"
    __table_args__ = (UniqueConstraint("tenant"),)

    tenant: str = Field(nullable=False)
    version: int = Field(default=0, nullable=False)
    rules: str = Field(default="[]", nullable=False)
    examples: str = Field(default="[]", nullable=False)
    summary: str = Field(default="", nullable=False)
    evidence_seq_start: int = Field(default=0, nullable=False)
    evidence_seq_end: int = Field(default=0, nullable=False)
    evidence_count: int = Field(default=0, nullable=False)
    learn_count: int = Field(default=0, nullable=False)
    use_count: int = Field(default=0, nullable=False)
    last_used_at: datetime | None = Field(default=None, nullable=True)
    manual_override: str = Field(default="", nullable=False)
    manual_version: int = Field(default=0, nullable=False)
    needs_rebuild: bool = Field(default=False, nullable=False)
    created_at: datetime = Field(default_factory=utc_now_naive, nullable=False)
    updated_at: datetime = Field(default_factory=utc_now_naive, nullable=False)


class ChatJargon(BaseIDModel, table=True):
    """群内特有词与释义；低置信释义只保存不注入。"""

    __tablename__ = "chat_jargon"
    __table_args__ = (
        UniqueConstraint("tenant", "term"),
        Index("ix_chat_jargon_tenant_status", "tenant", "status"),
    )

    tenant: str = Field(nullable=False)
    term: str = Field(nullable=False)
    normalized: str = Field(default="", nullable=False)
    meaning: str = Field(default="", nullable=False)
    evidence: str = Field(default="[]", nullable=False)
    last_inference_count: int = Field(default=0, nullable=False)
    count: int = Field(default=1, nullable=False)
    status: str = Field(default="active", nullable=False)
    source_batch_id: str = Field(default="", nullable=False)
    learn_count: int = Field(default=1, nullable=False)
    use_count: int = Field(default=0, nullable=False)
    first_seen: datetime = Field(default_factory=utc_now_naive, nullable=False)
    last_seen: datetime = Field(default_factory=utc_now_naive, nullable=False)
    last_used_at: datetime | None = Field(default=None, nullable=True)


class ChatExpression(BaseIDModel, table=True):
    """情境到说法的表达参考；复审通过后才注入。"""

    __tablename__ = "chat_expression"
    __table_args__ = (
        UniqueConstraint("tenant", "situation_hash", "style_hash"),
        Index("ix_chat_expression_tenant_count", "tenant", "count"),
    )

    tenant: str = Field(nullable=False)
    situation: str = Field(nullable=False)
    situation_hash: str = Field(nullable=False)
    style: str = Field(nullable=False)
    style_hash: str = Field(nullable=False)
    type: str = Field(default="phrase", nullable=False)
    count: float = Field(default=0.0, nullable=False)
    checked: bool = Field(default=False, nullable=False)
    manual_checked: bool = Field(default=False, nullable=False)
    rejected: bool = Field(default=False, nullable=False)
    source_message_seq: int = Field(default=0, nullable=False)
    source_batch_id: str = Field(default="", nullable=False)
    contributions: str = Field(default="[]", nullable=False)
    learn_count: int = Field(default=1, nullable=False)
    use_count: int = Field(default=0, nullable=False)
    last_used_at: datetime | None = Field(default=None, nullable=True)
    created_at: datetime = Field(default_factory=utc_now_naive, nullable=False)
    updated_at: datetime = Field(default_factory=utc_now_naive, nullable=False)


class ChatPlannerSample(BaseIDModel, table=True):
    """一轮规划的首轮动作与特征，用于门控校准和追溯。"""

    __tablename__ = "chat_planner_sample"
    __table_args__ = (
        UniqueConstraint("tenant", "turn_id"),
        Index("ix_chat_planner_sample_tenant_created", "tenant", "created_at"),
    )

    tenant: str = Field(nullable=False)
    turn_id: str = Field(nullable=False)
    trigger_kind: str = Field(default="", nullable=False)
    action: str = Field(default="", nullable=False)
    label: int = Field(default=0, nullable=False)
    features: str = Field(default="{}", nullable=False)
    provider: str = Field(default="", nullable=False)
    model: str = Field(default="", nullable=False)
    created_at: datetime = Field(default_factory=utc_now_naive, nullable=False)


class ChatEmoji(BaseIDModel, table=True):
    """表情包索引；文件保存在 ai_chat/emoji。"""

    __tablename__ = "chat_emoji"
    __table_args__ = (UniqueConstraint("tenant", "hash"),)

    tenant: str = Field(nullable=False)
    hash: str = Field(nullable=False)
    path: str = Field(nullable=False)
    description: str = Field(default="", nullable=False)
    tags: str = Field(default="[]", nullable=False)
    count: int = Field(default=0, nullable=False)
    last_used: datetime | None = Field(default=None, nullable=True)
    enabled: bool = Field(default=True, nullable=False)
    # 旧数据迁移后默认为已注册；采集入口显式写入 False
    registered: bool = Field(default=True, nullable=False)
    vlm_processed: bool = Field(default=True, nullable=False)
    register_time: datetime | None = Field(default=None, nullable=True)
    created_at: datetime = Field(default_factory=utc_now_naive, nullable=False)


class ChatTrace(BaseIDModel, table=True):
    """每轮注入与调用的最小追踪；保留 72 小时、全局最多 10000 条。"""

    __tablename__ = "chat_trace"
    __table_args__ = (Index("ix_chat_trace_tenant_created", "tenant", "created_at"),)

    tenant: str = Field(nullable=False)
    turn_id: str = Field(default="", nullable=False)
    trigger_kind: str = Field(default="", nullable=False)
    request_preview: str = Field(default="", nullable=False)
    injected: str = Field(default="{}", nullable=False)
    budget: str = Field(default="{}", nullable=False)
    provider: str = Field(default="", nullable=False)
    latency_ms: int = Field(default=0, nullable=False)
    error: str = Field(default="", nullable=False)
    created_at: datetime = Field(default_factory=utc_now_naive, nullable=False)
