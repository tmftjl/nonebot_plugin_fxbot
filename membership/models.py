"""会员群、续费码和续费记录模型。"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import UniqueConstraint
from sqlmodel import Field

from ..db import BaseIDModel, exec_list

exec_list.extend([])


def utc_now() -> datetime:
    """返回当前 UTC 时间。"""
    return datetime.now(timezone.utc)


class MembershipGroup(BaseIDModel, table=True):
    """会员群记录。"""

    __tablename__ = "membership_groups"
    __table_args__ = (UniqueConstraint("group_id"),)

    group_id: str = Field(index=True, nullable=False)
    status: str = Field(default="active", nullable=False)
    expires_at: datetime | None = Field(default=None, nullable=True)
    managed_by_bot: str | None = Field(default=None, nullable=True)
    remark: str | None = Field(default=None, nullable=True)
    last_reminder_on: str | None = Field(default=None, nullable=True)
    expired_at: datetime | None = Field(default=None, nullable=True)
    created_at: datetime = Field(default_factory=utc_now, nullable=False)
    updated_at: datetime = Field(default_factory=utc_now, nullable=False)


class RenewCode(BaseIDModel, table=True):
    """续费码记录。"""

    __tablename__ = "renew_codes"
    __table_args__ = (UniqueConstraint("code"),)

    code: str = Field(index=True, nullable=False)
    duration_value: int = Field(nullable=False)
    duration_unit: str = Field(nullable=False)
    max_use: int = Field(default=1, nullable=False)
    used_count: int = Field(default=0, nullable=False)
    expires_at: datetime | None = Field(default=None, nullable=True)
    status: str = Field(default="active", nullable=False)
    created_at: datetime = Field(default_factory=utc_now, nullable=False)
    updated_at: datetime = Field(default_factory=utc_now, nullable=False)


class RenewRecord(BaseIDModel, table=True):
    """续费审计记录。"""

    __tablename__ = "renew_records"

    code: str = Field(index=True, nullable=False)
    group_id: str = Field(index=True, nullable=False)
    operator_user_id: str | None = Field(default=None, nullable=True)
    used_at: datetime = Field(default_factory=utc_now, nullable=False)
    before_expires_at: datetime | None = Field(default=None, nullable=True)
    after_expires_at: datetime | None = Field(default=None, nullable=True)
