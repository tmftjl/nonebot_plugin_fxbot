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
    backfilled_at: datetime | None = Field(default=None, nullable=True)
    learn_enabled: bool | None = Field(default=None, nullable=True)
    facts_version: int = Field(default=0, nullable=False)
    episodes_version: int = Field(default=0, nullable=False)
    profile_version: int = Field(default=0, nullable=False)
    knowledge_version: int = Field(default=0, nullable=False)
    degraded_reason: str = Field(default="", nullable=False)
    updated_at: datetime = Field(default_factory=utc_now_naive, nullable=False)


class ChatJobCursor(BaseIDModel, table=True):
    """每个租户、每种记忆任务的独立游标。"""

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


class ChatJobBatch(BaseIDModel, table=True):
    """批次幂等边界与执行状态。"""

    __tablename__ = "chat_job_batch"
    __table_args__ = (
        UniqueConstraint("tenant", "job", "range_start", "range_end", "algorithm_version"),
        Index("ix_chat_job_batch_tenant_status", "tenant", "status"),
    )

    tenant: str = Field(nullable=False)
    job: str = Field(default="memory", nullable=False)
    range_start: int = Field(nullable=False)
    range_end: int = Field(nullable=False)
    algorithm_version: int = Field(default=1, nullable=False)
    status: str = Field(default="pending", nullable=False)
    attempt: int = Field(default=0, nullable=False)
    error: str = Field(default="", nullable=False)
    completed_at: datetime | None = Field(default=None, nullable=True)
    created_at: datetime = Field(default_factory=utc_now_naive, nullable=False)


class ChatEpisode(BaseIDModel, table=True):
    """抽取出的主题片段，保留来源消息范围。"""

    __tablename__ = "chat_episode"
    __table_args__ = (
        UniqueConstraint("tenant", "episode_key"),
        Index("ix_chat_episode_tenant_status", "tenant", "status"),
    )

    tenant: str = Field(nullable=False)
    episode_key: str = Field(nullable=False)
    batch_id: str = Field(default="", nullable=False)
    topic_index: int = Field(default=0, nullable=False)
    seq_start: int = Field(default=0, nullable=False)
    seq_end: int = Field(default=0, nullable=False)
    title: str = Field(default="", nullable=False)
    keywords: str = Field(default="[]", nullable=False)
    summary: str = Field(default="", nullable=False)
    participants: str = Field(default="[]", nullable=False)
    emotion_valence: float | None = Field(default=None, nullable=True)
    emotion_arousal: float | None = Field(default=None, nullable=True)
    status: str = Field(default="active", nullable=False)
    source_revision: int = Field(default=0, nullable=False)
    created_at: datetime = Field(default_factory=utc_now_naive, nullable=False)
    updated_at: datetime = Field(default_factory=utc_now_naive, nullable=False)


class ChatMemory(BaseIDModel, table=True):
    """长期事实；同一主体同一个谓词允许多个值。"""

    __tablename__ = "chat_memory"
    __table_args__ = (
        UniqueConstraint("tenant", "subject_id", "key", "value_hash"),
        Index("ix_chat_memory_tenant_subject", "tenant", "subject_id", "status"),
    )

    tenant: str = Field(nullable=False)
    subject_id: str = Field(nullable=False)
    subject_name: str = Field(default="", nullable=False)
    asserted_by: str = Field(default="", nullable=False)
    key: str = Field(nullable=False)
    value: str = Field(default="", nullable=False)
    value_hash: str = Field(default="", nullable=False)
    category: str = Field(default="other", nullable=False)
    confidence: float = Field(default=0.6, nullable=False)
    importance: float = Field(default=0.5, nullable=False)
    status: str = Field(default="active", nullable=False)
    source: str = Field(default="extract", nullable=False)
    source_episode_id: int | None = Field(default=None, nullable=True)
    source_batch_id: str = Field(default="", nullable=False)
    evidence_count: int = Field(default=0, nullable=False)
    valid_evidence_count: int = Field(default=0, nullable=False)
    hits: int = Field(default=0, nullable=False)
    last_hit_at: datetime | None = Field(default=None, nullable=True)
    asserted_at: datetime = Field(default_factory=utc_now_naive, nullable=False)
    expires_at: datetime | None = Field(default=None, nullable=True)
    sensitive: bool = Field(default=False, nullable=False)
    version: int = Field(default=0, nullable=False)
    created_at: datetime = Field(default_factory=utc_now_naive, nullable=False)
    updated_at: datetime = Field(default_factory=utc_now_naive, nullable=False)


class ChatMemoryEvidence(BaseIDModel, table=True):
    """事实到原始消息的反查证据。"""

    __tablename__ = "chat_memory_evidence"
    __table_args__ = (
        UniqueConstraint("tenant", "memory_id", "message_seq"),
        Index("ix_chat_memory_evidence_tenant_message", "tenant", "message_seq"),
        Index("ix_chat_memory_evidence_tenant_status", "tenant", "memory_id", "status"),
    )

    tenant: str = Field(nullable=False)
    memory_id: int = Field(nullable=False)
    message_seq: int = Field(nullable=False)
    platform_message_id: str = Field(default="", nullable=False)
    snippet: str = Field(default="", nullable=False)
    status: str = Field(default="valid", nullable=False)
    created_at: datetime = Field(default_factory=utc_now_naive, nullable=False)


class ChatForgetMarker(BaseIDModel, table=True):
    """禁止重建的主体或事实指纹。"""

    __tablename__ = "chat_forget_marker"
    __table_args__ = (
        UniqueConstraint("tenant", "scope", "subject_id", "fact_key", "fact_value_hash"),
        Index("ix_chat_forget_marker_tenant_subject", "tenant", "subject_id"),
    )

    tenant: str = Field(nullable=False)
    scope: str = Field(default="subject", nullable=False)
    subject_id: str = Field(default="", nullable=False)
    fact_key: str = Field(default="", nullable=False)
    fact_value_hash: str = Field(default="", nullable=False)
    reason: str = Field(default="", nullable=False)
    created_at: datetime = Field(default_factory=utc_now_naive, nullable=False)


class ChatAlias(BaseIDModel, table=True):
    """租户内的昵称别名，不跨群合并。"""

    __tablename__ = "chat_alias"
    __table_args__ = (
        UniqueConstraint("tenant", "user_id", "alias"),
        Index("ix_chat_alias_tenant_normalized", "tenant", "normalized"),
    )

    tenant: str = Field(nullable=False)
    user_id: str = Field(nullable=False)
    alias: str = Field(nullable=False)
    normalized: str = Field(default="", nullable=False)
    source: str = Field(default="nickname", nullable=False)
    first_seen: datetime = Field(default_factory=utc_now_naive, nullable=False)
    last_seen: datetime = Field(default_factory=utc_now_naive, nullable=False)
    confirmed: bool = Field(default=False, nullable=False)


class ChatProfile(BaseIDModel, table=True):
    """主体画像；stale 期间不注入。"""

    __tablename__ = "chat_profile"
    __table_args__ = (UniqueConstraint("tenant", "subject_id"),)

    tenant: str = Field(nullable=False)
    subject_id: str = Field(nullable=False)
    display_name: str = Field(default="", nullable=False)
    sections: str = Field(default="{}", nullable=False)
    summary: str = Field(default="", nullable=False)
    evidence_ids: str = Field(default="[]", nullable=False)
    source_versions: str = Field(default="{}", nullable=False)
    manual_override: str = Field(default="", nullable=False)
    manual_version: int = Field(default=0, nullable=False)
    version: int = Field(default=0, nullable=False)
    stale: bool = Field(default=False, nullable=False)
    stale_reason: str = Field(default="", nullable=False)
    last_built_at: datetime | None = Field(default=None, nullable=True)
    created_at: datetime = Field(default_factory=utc_now_naive, nullable=False)
    updated_at: datetime = Field(default_factory=utc_now_naive, nullable=False)


class ChatRelation(BaseIDModel, table=True):
    """主体到用户/组织/地点/物品的类型化关系。"""

    __tablename__ = "chat_relation"
    __table_args__ = (
        UniqueConstraint("tenant", "subject_id", "predicate", "object_kind", "object_key"),
        Index("ix_chat_relation_tenant_object", "tenant", "object_key"),
    )

    tenant: str = Field(nullable=False)
    subject_id: str = Field(nullable=False)
    predicate: str = Field(nullable=False)
    object_kind: str = Field(default="user", nullable=False)
    object_key: str = Field(nullable=False)
    object_name: str = Field(default="", nullable=False)
    object_user_id: str = Field(default="", nullable=False)
    confidence: float = Field(default=0.6, nullable=False)
    source_memory_id: int | None = Field(default=None, nullable=True)
    status: str = Field(default="active", nullable=False)
    created_at: datetime = Field(default_factory=utc_now_naive, nullable=False)
    updated_at: datetime = Field(default_factory=utc_now_naive, nullable=False)
