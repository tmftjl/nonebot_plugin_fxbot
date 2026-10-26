"""记忆历史的租户级持久化。"""

from __future__ import annotations

from datetime import datetime

from sqlmodel import select
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ...db import with_session
from .models import ChatTurn, ChatMessage, ChatTurnItem, ChatSessionState, utc_now_naive


class ChatRepository:
    """只暴露按 tenant 隔离的读写，不跨租户查询。"""

    @with_session
    async def append_message(self, session: AsyncSession, message: ChatMessage) -> int:
        """按 dedupe_key 幂等写入，已存在时保留原行。"""
        values = message.model_dump(exclude={"id"})
        statement = (
            sqlite_insert(ChatMessage).values(**values).on_conflict_do_nothing(index_elements=["tenant", "dedupe_key"])
        )
        await session.execute(statement)
        rows = await ChatMessage.select_rows(tenant=message.tenant, dedupe_key=message.dedupe_key, session=session)
        return int(rows[0].id)

    @with_session
    async def update_message_content(self, session: AsyncSession, tenant: str, message_id: int, content: str) -> None:
        """更新图片描述等晚到内容；已撤回的行不再覆盖。"""
        await session.execute(
            update(ChatMessage)
            .where(
                ChatMessage.tenant == tenant,
                ChatMessage.id == message_id,
                ChatMessage.deleted.is_(False),
            )
            .values(content=content)
        )

    @with_session
    async def get_recent_messages(self, session: AsyncSession, tenant: str, limit: int) -> list[dict[str, object]]:
        """按时间合并原始消息与已发出的助手回复。"""
        if limit <= 0:
            return []
        message_stmt = (
            select(ChatMessage)
            .where(
                ChatMessage.tenant == tenant,
                ChatMessage.kind == "user",
                ChatMessage.deleted.is_(False),
            )
            .order_by(ChatMessage.id.desc())
            .limit(limit)
        )
        messages = list((await session.execute(message_stmt)).scalars().all())[::-1]
        item_stmt = (
            select(ChatTurnItem)
            .join(ChatTurn, (ChatTurn.id == ChatTurnItem.turn_id) & (ChatTurn.tenant == ChatTurnItem.tenant))
            .where(
                ChatTurnItem.tenant == tenant,
                ChatTurnItem.role == "assistant",
                ChatTurnItem.visible.is_(True),
                ChatTurn.status.in_(("sent", "partial")),
            )
            .order_by(ChatTurnItem.id.desc())
            .limit(limit)
        )
        items = list((await session.execute(item_stmt)).scalars().all())[::-1]

        entries: list[tuple[datetime, int, dict[str, object]]] = []
        for row in messages:
            if row.content:
                entries.append((row.occurred_at or row.ingested_at, 0, {"role": "user", "content": row.content}))
        for row in items:
            if row.content:
                entries.append((row.created_at, 1, {"role": "assistant", "content": row.content}))
        entries.sort(key=lambda entry: (entry[0], entry[1]))
        return [entry[2] for entry in entries[-limit:]]

    @with_session
    async def upsert_turn(self, session: AsyncSession, turn: ChatTurn) -> int:
        """创建轮次；重试同一 turn_key 时保留首次记录。"""
        values = turn.model_dump(exclude={"id"})
        statement = (
            sqlite_insert(ChatTurn).values(**values).on_conflict_do_nothing(index_elements=["tenant", "turn_key"])
        )
        await session.execute(statement)
        rows = await ChatTurn.select_rows(tenant=turn.tenant, turn_key=turn.turn_key, session=session)
        return int(rows[0].id)

    @with_session
    async def update_turn(self, session: AsyncSession, tenant: str, turn_key: str, **fields: object) -> None:
        """只更新传入的轮次字段。"""
        if not fields:
            return
        await session.execute(
            update(ChatTurn).where(ChatTurn.tenant == tenant, ChatTurn.turn_key == turn_key).values(**fields)
        )

    @with_session
    async def append_turn_item(self, session: AsyncSession, item: ChatTurnItem) -> int:
        """按 (tenant, turn_id, item_seq) 幂等写入轮内条目。"""
        values = item.model_dump(exclude={"id"})
        statement = (
            sqlite_insert(ChatTurnItem)
            .values(**values)
            .on_conflict_do_update(
                index_elements=["tenant", "turn_id", "item_seq"],
                set_={
                    "content": item.content,
                    "payload": item.payload,
                    "source_message_id": item.source_message_id,
                    "visible": item.visible,
                    "send_status": item.send_status,
                    "platform_message_id": item.platform_message_id,
                },
            )
        )
        await session.execute(statement)
        rows = await ChatTurnItem.select_rows(
            tenant=item.tenant, turn_id=item.turn_id, item_seq=item.item_seq, session=session
        )
        return int(rows[0].id)

    @with_session
    async def update_turn_item(self, session: AsyncSession, tenant: str, item_id: int, **fields: object) -> None:
        """只更新传入的轮内条目字段。"""
        if not fields:
            return
        await session.execute(
            update(ChatTurnItem).where(ChatTurnItem.tenant == tenant, ChatTurnItem.id == item_id).values(**fields)
        )

    @with_session
    async def mark_turn_item_sent(
        self, session: AsyncSession, tenant: str, item_id: int, platform_message_id: str | None = None
    ) -> bool:
        """发送成功后让助手条目进入历史，并把轮次标为已发送；重复调用返回 False。"""
        rows = await ChatTurnItem.select_rows(tenant=tenant, id=item_id, session=session)
        if not rows:
            return False
        item = rows[0]
        if item.send_status == "sent":
            return False
        await session.execute(
            update(ChatTurnItem)
            .where(ChatTurnItem.tenant == tenant, ChatTurnItem.id == item_id)
            .values(visible=True, send_status="sent", platform_message_id=platform_message_id)
        )
        await session.execute(
            update(ChatTurn)
            .where(ChatTurn.tenant == tenant, ChatTurn.id == item.turn_id)
            .values(status="sent", finished_at=utc_now_naive())
        )
        return True

    @with_session
    async def clear_tenant(self, session: AsyncSession, tenant: str) -> int:
        """删除该租户的消息与轮次，递增代次并返回新代次。"""
        await session.execute(delete(ChatTurnItem).where(ChatTurnItem.tenant == tenant))
        await session.execute(delete(ChatTurn).where(ChatTurn.tenant == tenant))
        await session.execute(delete(ChatMessage).where(ChatMessage.tenant == tenant))
        states = await ChatSessionState.select_rows(tenant=tenant, session=session)
        state = states[0] if states else None
        timestamp = utc_now_naive()
        if state is None:
            state = ChatSessionState(tenant=tenant, generation=1, cleared_at=timestamp, updated_at=timestamp)
            session.add(state)
        else:
            state.generation += 1
            state.cleared_at = timestamp
            state.updated_at = timestamp
        return state.generation

    @with_session
    async def get_session_state(self, session: AsyncSession, tenant: str) -> ChatSessionState | None:
        """读取会话代次与记忆开关。"""
        states = await ChatSessionState.select_rows(tenant=tenant, session=session)
        return states[0] if states else None


chat_repository = ChatRepository()
