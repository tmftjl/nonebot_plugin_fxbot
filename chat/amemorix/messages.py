"""A_Memorix 读取聊天记录的异步查询。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func
from sqlmodel import select

from ...db import base_models
from ..memory.models import ChatMessage


def _row(message: ChatMessage) -> dict[str, Any]:
    return {
        "message_id": str(message.platform_message_id or message.id or ""),
        "sender_id": str(message.sender_id or ""),
        "sender_name": str(message.sender_name or ""),
        "content": str(message.content or ""),
        "occurred_at": message.occurred_at,
        "platform": str(message.platform or ""),
    }


async def count_messages(tenant: str, end_time: datetime | None = None) -> int:
    if base_models.async_maker is None:
        return 0
    try:
        async with base_models.async_maker() as session:
            stmt = select(func.count()).select_from(ChatMessage).where(
                ChatMessage.tenant == tenant, ChatMessage.deleted == False  # noqa: E712
            )
            if end_time is not None:
                stmt = stmt.where(ChatMessage.occurred_at <= end_time)
            return int((await session.execute(stmt)).scalar_one() or 0)
    except Exception:
        return 0


async def recent_messages(tenant: str, limit: int, *, before: datetime | None = None) -> list[dict[str, Any]]:
    """按时间升序返回最近消息。"""
    if base_models.async_maker is None:
        return []
    try:
        async with base_models.async_maker() as session:
            stmt = select(ChatMessage).where(
                ChatMessage.tenant == tenant, ChatMessage.deleted == False  # noqa: E712
            )
            if before is not None:
                stmt = stmt.where(ChatMessage.occurred_at <= before)
            stmt = stmt.order_by(ChatMessage.occurred_at.desc(), ChatMessage.id.desc()).limit(max(1, int(limit)))
            rows = list((await session.execute(stmt)).scalars().all())
    except Exception:
        return []
    rows.reverse()
    return [_row(message) for message in rows]


async def sender_messages(tenant: str, sender_id: str, limit: int, *, before: datetime | None = None) -> list[dict[str, Any]]:
    """某人的最近发言，按时间升序。"""
    if base_models.async_maker is None:
        return []
    try:
        async with base_models.async_maker() as session:
            stmt = select(ChatMessage).where(
                ChatMessage.tenant == tenant,
                ChatMessage.sender_id == sender_id,
                ChatMessage.deleted == False,  # noqa: E712
            )
            if before is not None:
                stmt = stmt.where(ChatMessage.occurred_at <= before)
            stmt = stmt.order_by(ChatMessage.occurred_at.desc(), ChatMessage.id.desc()).limit(max(1, int(limit)))
            rows = list((await session.execute(stmt)).scalars().all())
    except Exception:
        return []
    rows.reverse()
    return [_row(message) for message in rows]
