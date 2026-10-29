"""A_Memorix 用到的人物身份表。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Index, UniqueConstraint
from sqlmodel import Field

from ...db import BaseIDModel
from ...utils.tz import now, to_naive_utc


class ChatPerson(BaseIDModel, table=True):
    """租户内的人物身份；person_id 由租户、平台和用户号决定。"""

    __tablename__ = "chat_person"
    __table_args__ = (
        UniqueConstraint("tenant", "person_id"),
        Index("ix_chat_person_tenant_user", "tenant", "platform", "user_id"),
    )

    tenant: str = Field(nullable=False)
    person_id: str = Field(nullable=False)
    platform: str = Field(default="", nullable=False)
    user_id: str = Field(default="", nullable=False)
    person_name: str = Field(default="", nullable=False)
    user_nickname: str = Field(default="", nullable=False)
    group_cardname: str = Field(default="[]", nullable=False)
    memory_points: str = Field(default="{}", nullable=False)
    created_at: datetime = Field(default_factory=lambda: to_naive_utc(now()), nullable=False)
    updated_at: datetime = Field(default_factory=lambda: to_naive_utc(now()), nullable=False)
