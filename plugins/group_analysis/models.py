"""群聊日报记录模型。"""

from __future__ import annotations

from typing import Any
from datetime import datetime

from sqlmodel import Field, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import BaseIDModel, with_session
from ...utils.tz import now, to_naive_utc


def utc_now_naive() -> datetime:
    """返回当前时刻的 naive UTC。"""
    return to_naive_utc(now())


class GroupAnalysisReport(BaseIDModel, table=True):
    """一次日报的统计、模型结果与长图位置。"""

    __tablename__ = "group_analysis_reports"

    group_id: str = Field(index=True, nullable=False)
    report_date: str = Field(index=True, max_length=10, nullable=False)
    period_start: int = Field(default=0, nullable=False)
    period_end: int = Field(default=0, nullable=False)
    message_count: int = Field(default=0, nullable=False)
    participant_count: int = Field(default=0, nullable=False)
    summary: str = Field(default="{}", nullable=False)
    image_path: str = Field(default="", nullable=False)
    created_at: datetime = Field(default_factory=utc_now_naive, nullable=False)

    @classmethod
    @with_session
    async def upsert(cls, session: AsyncSession, **fields: Any) -> None:
        """同一群同一天只保留一行，自动日报重跑时覆盖旧结果。"""
        stmt = select(cls).where(cls.group_id == fields["group_id"], cls.report_date == fields["report_date"])
        row = (await session.execute(stmt)).scalars().first()
        if row is None:
            session.add(cls(**fields))
            return
        for key, value in fields.items():
            setattr(row, key, value)
