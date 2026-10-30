"""记忆历史的租户级持久化。"""

from __future__ import annotations

import json
from typing import Any
from datetime import datetime, timedelta
from dataclasses import dataclass

from nonebot import logger
from sqlmodel import select
from sqlalchemy import func, delete, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ...db import with_session
from .models import (
    ChatTurn,
    ChatEmoji,
    ChatStyle,
    ChatTrace,
    ChatJargon,
    ChatMessage,
    ChatTurnItem,
    ChatJobCursor,
    ChatExpression,
    ChatSessionState,
    ChatPlannerSample,
    ChatRecallTombstone,
    utc_now_naive,
)


def _json_strings(value: object) -> set[str]:
    """解析 JSON 字符串数组；坏数据按空处理。"""
    try:
        items = json.loads(str(value or "[]"))
    except (TypeError, ValueError):
        return set()
    return {str(item) for item in items} if isinstance(items, list) else set()


def _batch_range(value: object) -> tuple[str, int, int] | None:
    """解析「前缀:起:止」来源批次；格式不符返回 None。"""
    parts = str(value or "").split(":")
    if len(parts) != 3:
        return None
    try:
        return parts[0], int(parts[1]), int(parts[2])
    except ValueError:
        return None


def _add_contribution(raw: object, batch_id: str, delta: float, learned: int) -> str:
    """按批次累加对已有表达的贡献，撤回时据此回滚。"""
    try:
        items = json.loads(str(raw or "[]"))
    except (TypeError, ValueError):
        items = []
    entries = [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []
    for entry in entries:
        if str(entry.get("batch") or "") == batch_id:
            entry["count"] = float(entry.get("count") or 0) + delta
            entry["learn"] = int(entry.get("learn") or 0) + learned
            break
    else:
        entries.append({"batch": batch_id, "count": delta, "learn": learned})
    return json.dumps(entries, ensure_ascii=False)


@dataclass(frozen=True)
class TenantJobState:
    """调度所需的租户消息与学习开关快照。"""

    tenant: str
    last_message_id: int
    message_count: int
    learn_enabled: bool | None


class ChatRepository:
    """只暴露按 tenant 隔离的读写，不跨租户查询。"""

    async def _lock_generation(self, session: AsyncSession, tenant: str) -> int:
        """先取得 SQLite 写锁再读取代次，避免校验与写入之间被清空。"""
        await session.execute(
            update(ChatSessionState)
            .where(ChatSessionState.tenant == tenant)
            .values(generation=ChatSessionState.generation)
        )
        states = await ChatSessionState.select_rows(tenant=tenant, session=session)
        return states[0].generation if states else 0

    async def _current_turn(
        self,
        session: AsyncSession,
        tenant: str,
        *,
        turn_id: int | None = None,
        turn_key: str | None = None,
        generation: int | None = None,
    ) -> bool:
        """校验轮次仍存在且属于当前代次，调用方在同一事务内写入。"""
        current = await self._lock_generation(session, tenant)
        conditions = {"id": turn_id} if turn_id is not None else {"turn_key": turn_key}
        rows = await ChatTurn.select_rows(tenant=tenant, session=session, **conditions)
        if not rows or rows[0].generation != current or (generation is not None and generation != current):
            logger.debug(
                "[Chat] 跳过失效轮次写入: tenant={} turn_id={} turn_key={} generation={} current={}",
                tenant,
                turn_id,
                turn_key,
                generation,
                current,
            )
            return False
        return True

    @with_session
    async def append_message(
        self, session: AsyncSession, message: ChatMessage, *, generation: int | None = None
    ) -> int | None:
        """按 dedupe_key 幂等写入，已存在时保留原行。"""
        if generation is not None and generation != await self._lock_generation(session, message.tenant):
            logger.debug("[Chat] 跳过过期用户消息写入: tenant={} generation={}", message.tenant, generation)
            return None
        values = message.model_dump(exclude={"id"})
        statement = (
            sqlite_insert(ChatMessage).values(**values).on_conflict_do_nothing(index_elements=["tenant", "dedupe_key"])
        )
        await session.execute(statement)
        if message.platform_message_id:
            tombstones = await ChatRecallTombstone.select_rows(
                tenant=message.tenant, platform_message_id=message.platform_message_id, session=session
            )
            if tombstones:
                tombstone = tombstones[0]
                await session.execute(
                    update(ChatMessage)
                    .where(
                        ChatMessage.tenant == message.tenant,
                        ChatMessage.platform_message_id == message.platform_message_id,
                    )
                    .values(deleted=True, deleted_at=tombstone.deleted_at, delete_operator=tombstone.delete_operator)
                )
                await session.execute(
                    delete(ChatRecallTombstone).where(
                        ChatRecallTombstone.tenant == message.tenant,
                        ChatRecallTombstone.platform_message_id == message.platform_message_id,
                    )
                )
        rows = await ChatMessage.select_rows(tenant=message.tenant, dedupe_key=message.dedupe_key, session=session)
        return int(rows[0].id)

    @with_session
    async def update_message_content(
        self,
        session: AsyncSession,
        tenant: str,
        message_id: int,
        content: str,
        *,
        turn_key: str | None = None,
        generation: int | None = None,
    ) -> None:
        """更新图片描述等晚到内容；已撤回的行不再覆盖。"""
        if turn_key is not None and not await self._current_turn(
            session, tenant, turn_key=turn_key, generation=generation
        ):
            return
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
                message: dict[str, object] = {"role": "user", "content": row.content}
                if row.platform_message_id:
                    message["_source_ids"] = [row.platform_message_id]
                if row.sender_id:
                    message["_sender_id"] = row.sender_id
                entries.append((row.occurred_at or row.ingested_at, 0, message))
        for row in items:
            if row.content:
                entries.append((row.created_at, 1, {"role": "assistant", "content": row.content}))
        entries.sort(key=lambda entry: (entry[0], entry[1]))
        return [entry[2] for entry in entries[-limit:]]

    @with_session
    async def upsert_turn(self, session: AsyncSession, turn: ChatTurn) -> int | None:
        """创建轮次；重试同一 turn_key 时保留首次记录。"""
        if turn.generation != await self._lock_generation(session, turn.tenant):
            logger.debug("[Chat] 跳过过期轮次创建: tenant={} generation={}", turn.tenant, turn.generation)
            return None
        values = turn.model_dump(exclude={"id"})
        statement = (
            sqlite_insert(ChatTurn).values(**values).on_conflict_do_nothing(index_elements=["tenant", "turn_key"])
        )
        await session.execute(statement)
        rows = await ChatTurn.select_rows(tenant=turn.tenant, turn_key=turn.turn_key, session=session)
        return int(rows[0].id)

    @with_session
    async def update_turn(
        self, session: AsyncSession, tenant: str, turn_key: str, *, generation: int | None = None, **fields: object
    ) -> None:
        """只更新传入的轮次字段。"""
        if not fields or not await self._current_turn(session, tenant, turn_key=turn_key, generation=generation):
            return
        await session.execute(
            update(ChatTurn).where(ChatTurn.tenant == tenant, ChatTurn.turn_key == turn_key).values(**fields)
        )

    @with_session
    async def append_turn_item(
        self, session: AsyncSession, item: ChatTurnItem, *, generation: int | None = None
    ) -> int | None:
        """按 (tenant, turn_id, item_seq) 幂等写入轮内条目。"""
        if not await self._current_turn(session, item.tenant, turn_id=item.turn_id, generation=generation):
            return None
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
    async def update_turn_item(
        self, session: AsyncSession, tenant: str, item_id: int, *, generation: int | None = None, **fields: object
    ) -> None:
        """只更新传入的轮内条目字段。"""
        if not fields:
            return
        await self._lock_generation(session, tenant)
        rows = await ChatTurnItem.select_rows(tenant=tenant, id=item_id, session=session)
        if not rows or not await self._current_turn(session, tenant, turn_id=rows[0].turn_id, generation=generation):
            logger.debug("[Chat] 跳过失效轮次条目写入: tenant={} item_id={}", tenant, item_id)
            return
        await session.execute(
            update(ChatTurnItem).where(ChatTurnItem.tenant == tenant, ChatTurnItem.id == item_id).values(**fields)
        )

    @with_session
    async def mark_turn_item_sent(
        self,
        session: AsyncSession,
        tenant: str,
        item_id: int,
        platform_message_id: str | None = None,
        *,
        generation: int | None = None,
    ) -> bool:
        """发送成功后让助手条目进入历史；未发完时轮次记为部分发送，重启后前缀仍可见。"""
        await self._lock_generation(session, tenant)
        rows = await ChatTurnItem.select_rows(tenant=tenant, id=item_id, session=session)
        if not rows or not await self._current_turn(session, tenant, turn_id=rows[0].turn_id, generation=generation):
            logger.debug("[Chat] 跳过失效发送结果: tenant={} item_id={}", tenant, item_id)
            return False
        item = rows[0]
        if item.send_status == "sent":
            return False
        await session.execute(
            update(ChatTurnItem)
            .where(ChatTurnItem.tenant == tenant, ChatTurnItem.id == item_id)
            .values(visible=True, send_status="sent", platform_message_id=platform_message_id)
        )
        items = await ChatTurnItem.select_rows(tenant=tenant, turn_id=item.turn_id, session=session)
        assistant = [row for row in items if row.role == "assistant"]
        if assistant and all(row.id == item_id or row.send_status == "sent" for row in assistant):
            await session.execute(
                update(ChatTurn)
                .where(ChatTurn.tenant == tenant, ChatTurn.id == item.turn_id)
                .values(status="sent", finished_at=utc_now_naive())
            )
        else:
            await session.execute(
                update(ChatTurn).where(ChatTurn.tenant == tenant, ChatTurn.id == item.turn_id).values(status="partial")
            )
        return True

    @with_session
    async def clear_tenant(self, session: AsyncSession, tenant: str) -> int:
        """删除该租户的消息与轮次，递增代次并返回新代次。"""
        await session.execute(delete(ChatTurnItem).where(ChatTurnItem.tenant == tenant))
        await session.execute(delete(ChatTurn).where(ChatTurn.tenant == tenant))
        await session.execute(delete(ChatMessage).where(ChatMessage.tenant == tenant))
        await session.execute(delete(ChatPlannerSample).where(ChatPlannerSample.tenant == tenant))
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

    @with_session
    async def set_learn_enabled(self, session: AsyncSession, tenant: str, enabled: bool) -> None:
        """写入本租户的长期学习开关。"""
        state = await self._state_row(session, tenant)
        state.learn_enabled = enabled
        state.updated_at = utc_now_naive()

    @with_session
    async def set_proactive_enabled(self, session: AsyncSession, tenant: str, enabled: bool) -> None:
        """写入本群的主动接话开关。"""
        state = await self._state_row(session, tenant)
        state.proactive_enabled = enabled
        state.updated_at = utc_now_naive()

    @with_session
    async def get_cursor(self, session: AsyncSession, tenant: str, job: str = "memory") -> ChatJobCursor | None:
        """读取某类任务的实时游标。"""
        rows = await ChatJobCursor.select_rows(tenant=tenant, job=job, session=session)
        return rows[0] if rows else None

    @with_session
    async def set_cursor(self, session: AsyncSession, tenant: str, job: str, cursor_seq: int) -> None:
        """推进与产出解耦的任务扫描游标。"""
        await self._upsert_cursor(session, tenant, job, int(cursor_seq))

    @with_session
    async def scheduling_snapshot(self, session: AsyncSession) -> list[TenantJobState]:
        """一次读出所有有消息租户的消息量与学习开关。"""
        last_rows = (
            await session.execute(
                select(ChatMessage.tenant, func.max(ChatMessage.id), func.count(ChatMessage.id))
                .where(ChatMessage.kind == "user", ChatMessage.deleted.is_(False))
                .group_by(ChatMessage.tenant)
            )
        ).all()
        last_ids = {str(row[0]): (int(row[1] or 0), int(row[2] or 0)) for row in last_rows}
        if not last_ids:
            return []
        tenants = list(last_ids)
        states = {
            str(row.tenant): row
            for row in (await session.execute(select(ChatSessionState).where(ChatSessionState.tenant.in_(tenants))))
            .scalars()
            .all()
        }
        return [
            TenantJobState(
                tenant=tenant,
                last_message_id=last_id,
                message_count=count,
                learn_enabled=states[tenant].learn_enabled if tenant in states else None,
            )
            for tenant, (last_id, count) in last_ids.items()
        ]

    @with_session
    async def list_console_tenants(self, session: AsyncSession) -> list[dict[str, object]]:
        """返回有消息或学习记录的租户，附带消息数与学习、使用次数。"""
        count_rows = (
            await session.execute(
                select(ChatMessage.tenant, func.count(ChatMessage.id))
                .where(ChatMessage.kind == "user", ChatMessage.deleted.is_(False))
                .group_by(ChatMessage.tenant)
            )
        ).all()
        counts = {str(row[0]): int(row[1]) for row in count_rows}
        usage: dict[str, list[int]] = {}

        def merge_usage(rows: list[tuple[str, object, object]]) -> None:
            for tenant, learn_count, use_count in rows:
                bucket = usage.setdefault(str(tenant), [0, 0])
                bucket[0] += int(learn_count or 0)
                bucket[1] += int(use_count or 0)

        for model in (ChatStyle, ChatJargon, ChatExpression):
            merge_usage(
                (
                    await session.execute(
                        select(model.tenant, func.sum(model.learn_count), func.sum(model.use_count)).group_by(
                            model.tenant
                        )
                    )
                ).all()
            )
        tenants = sorted(set(counts) | set(usage))
        return [
            {
                "tenant": tenant,
                "messages": counts.get(tenant, 0),
                "learn_count": usage.get(tenant, [0, 0])[0],
                "use_count": usage.get(tenant, [0, 0])[1],
            }
            for tenant in sorted(tenants, key=lambda item: (-counts.get(item, 0), item))
        ]

    @with_session
    async def messages_after(
        self, session: AsyncSession, tenant: str, after_id: int, limit: int = 200
    ) -> list[ChatMessage]:
        """按主键顺序读取游标之后的人类消息，跳过已撤回。"""
        statement = (
            select(ChatMessage)
            .where(
                ChatMessage.tenant == tenant,
                ChatMessage.kind == "user",
                ChatMessage.id > after_id,
                ChatMessage.deleted.is_(False),
            )
            .order_by(ChatMessage.id.asc())
            .limit(limit)
        )
        return list((await session.execute(statement)).scalars().all())

    @with_session
    async def get_style(self, session: AsyncSession, tenant: str) -> ChatStyle | None:
        """读取租户的群风格；不存在时返回 None。"""
        rows = await ChatStyle.select_rows(tenant=tenant, session=session)
        return rows[0] if rows else None

    async def _evidence_stale(self, session: AsyncSession, tenant: str, evidence_ids: tuple[int, ...]) -> bool:
        """证据消息在模型调用期间被撤回时，学习结果作废。"""
        if not evidence_ids:
            return False
        result = await session.execute(
            select(ChatMessage.id)
            .where(
                ChatMessage.tenant == tenant,
                ChatMessage.id.in_(evidence_ids),
                ChatMessage.deleted.is_(True),
            )
            .limit(1)
        )
        return result.first() is not None

    @with_session
    async def save_style(
        self,
        session: AsyncSession,
        tenant: str,
        result: object,
        *,
        seq_start: int,
        seq_end: int,
        evidence_count: int,
        source_batch_id: str,
        expected_manual_version: int = 0,
        evidence_ids: tuple[int, ...] = (),
        expected_exists: bool = False,
    ) -> bool:
        """写入风格与黑话；人工覆盖版本变化、风格被删除或证据被撤回时放弃本次结果。"""
        from .style import StyleResult

        if not isinstance(result, StyleResult):
            return False
        if await self._evidence_stale(session, tenant, evidence_ids):
            return False
        now = utc_now_naive()
        rows = await ChatStyle.select_rows(tenant=tenant, session=session)
        if rows:
            style = rows[0]
            if int(style.manual_version) != int(expected_manual_version):
                return False
            style.summary = result.summary
            style.rules = json.dumps(list(result.rules), ensure_ascii=False)
            style.examples = json.dumps(
                [{"text": example.text, "seq": example.seq} for example in result.examples],
                ensure_ascii=False,
            )
            style.evidence_seq_start = int(seq_start)
            style.evidence_seq_end = int(seq_end)
            style.evidence_count = int(evidence_count)
            style.needs_rebuild = False
            style.version += 1
            style.learn_count += 1
            style.updated_at = now
        else:
            if expected_exists:
                return False
            style = ChatStyle(
                tenant=tenant,
                version=1,
                rules=json.dumps(list(result.rules), ensure_ascii=False),
                examples=json.dumps(
                    [{"text": example.text, "seq": example.seq} for example in result.examples],
                    ensure_ascii=False,
                ),
                summary=result.summary,
                evidence_seq_start=int(seq_start),
                evidence_seq_end=int(seq_end),
                evidence_count=int(evidence_count),
                learn_count=1,
            )
            session.add(style)

        for item in result.jargon:
            existing = await ChatJargon.select_rows(tenant=tenant, term=item.term, session=session)
            if existing:
                jargon = existing[0]
                jargon.count += 1
                jargon.learn_count += 1
                jargon.last_seen = now
                jargon.source_batch_id = source_batch_id
                if item.status == "active":
                    jargon.status = "active"
                    jargon.meaning = item.meaning
                    jargon.normalized = item.normalized
            else:
                session.add(
                    ChatJargon(
                        tenant=tenant,
                        term=item.term,
                        normalized=item.normalized,
                        meaning=item.meaning,
                        count=1,
                        status=item.status,
                        source_batch_id=source_batch_id,
                        learn_count=1,
                        first_seen=now,
                        last_seen=now,
                    )
                )
        return True

    @with_session
    async def mark_style_used(self, session: AsyncSession, tenant: str) -> None:
        """记录群风格实际进入注入。"""
        rows = await ChatStyle.select_rows(tenant=tenant, session=session)
        if rows:
            rows[0].use_count += 1
            rows[0].last_used_at = utc_now_naive()

    @with_session
    async def list_tenants(self, session: AsyncSession) -> list[str]:
        """返回有用户消息的租户，用于解析共享组。"""
        statement = (
            select(ChatMessage.tenant).where(ChatMessage.kind == "user", ChatMessage.deleted.is_(False)).distinct()
        )
        return [str(row[0]) for row in (await session.execute(statement)).all()]

    @with_session
    async def list_jargon(
        self,
        session: AsyncSession,
        tenant: str,
        *,
        status: str | None = None,
        limit: int = 500,
        tenants: list[str] | None = None,
    ) -> list[ChatJargon]:
        """按出现次数读取黑话条目；传入 tenants 时合并共享租户。"""
        scope = list(dict.fromkeys(tenants or [tenant]))
        statement = select(ChatJargon).where(ChatJargon.tenant.in_(scope))
        if status:
            statement = statement.where(ChatJargon.status == status)
        statement = statement.order_by(ChatJargon.count.desc(), ChatJargon.last_seen.desc()).limit(limit)
        return list((await session.execute(statement)).scalars().all())

    @with_session
    async def save_jargon_batch(
        self,
        session: AsyncSession,
        tenant: str,
        candidates: dict[str, list[int]],
        *,
        cursor_seq: int,
        expected_cursor: int,
        evidence_ids: tuple[int, ...],
    ) -> bool:
        """独立保存黑话候选、证据与游标，重复批次和已撤回证据不写入。"""
        if await self._evidence_stale(session, tenant, evidence_ids):
            return False
        cursors = await ChatJobCursor.select_rows(tenant=tenant, job="jargon", session=session)
        cursor = cursors[0] if cursors else None
        if (int(cursor.cursor_seq) if cursor else 0) != expected_cursor:
            return False
        for term, identifiers in candidates.items():
            rows = await ChatJargon.select_rows(tenant=tenant, term=term, session=session)
            if rows:
                row = rows[0]
                previous = json.loads(row.evidence or "[]")
                row.evidence = json.dumps(list(dict.fromkeys([*previous, *identifiers]))[-100:])
                row.count += 1
                row.learn_count += 1
                row.last_seen = utc_now_naive()
                row.source_batch_id = f"jargon:{min(evidence_ids)}:{cursor_seq}"
            else:
                session.add(
                    ChatJargon(
                        tenant=tenant,
                        term=term,
                        normalized=term.casefold(),
                        status="pending",
                        evidence=json.dumps(identifiers),
                        source_batch_id=f"jargon:{min(evidence_ids)}:{cursor_seq}",
                    )
                )
        if cursor is None:
            session.add(ChatJobCursor(tenant=tenant, job="jargon", cursor_seq=cursor_seq))
        else:
            cursor.cursor_seq = cursor_seq
        return True

    @with_session
    async def jargon_evidence(self, session: AsyncSession, tenant: str, identifiers: list[int]) -> list[ChatMessage]:
        """只读取当前会话中仍可见的黑话证据消息。"""
        statement = select(ChatMessage).where(
            ChatMessage.tenant == tenant,
            ChatMessage.id.in_(identifiers),
            ChatMessage.deleted.is_(False),
        )
        return list((await session.execute(statement)).scalars().all())

    @with_session
    async def learning_sources(
        self,
        session: AsyncSession,
        tenant: str,
        identifiers: list[str],
        dedupe_keys: list[str],
        contents: list[str] | None = None,
    ) -> list[ChatMessage]:
        """把裁切上下文映射回当前会话的真实人类消息。"""
        statement = (
            select(ChatMessage)
            .where(
                ChatMessage.tenant == tenant,
                ChatMessage.kind == "user",
                ChatMessage.deleted.is_(False),
                ChatMessage.platform_message_id.in_(identifiers)
                | ChatMessage.dedupe_key.in_(dedupe_keys)
                | (ChatMessage.platform_message_id.is_(None) & ChatMessage.content.in_(contents or [])),
            )
            .order_by(ChatMessage.id)
        )
        return list((await session.execute(statement)).scalars().all())

    @with_session
    async def message_by_identity(self, session: AsyncSession, tenant: str, identity: str) -> ChatMessage | None:
        """只解析当前会话内未撤回的原始消息。"""
        statement = (
            select(ChatMessage)
            .where(
                ChatMessage.tenant == tenant,
                ChatMessage.deleted.is_(False),
                (ChatMessage.platform_message_id == identity) | (ChatMessage.dedupe_key == identity),
            )
            .order_by(ChatMessage.id.desc())
            .limit(1)
        )
        return (await session.execute(statement)).scalars().first()

    @with_session
    async def save_jargon_inference(
        self,
        session: AsyncSession,
        tenant: str,
        identifier: int,
        *,
        expected_count: int,
        meaning: str,
        accepted: bool,
    ) -> bool:
        """释义只应用到仍存在且计数未变的当前会话候选。"""
        rows = await ChatJargon.select_rows(tenant=tenant, id=identifier, session=session)
        if not rows or rows[0].count != expected_count:
            return False
        row = rows[0]
        evidence = tuple(int(value) for value in json.loads(row.evidence or "[]"))
        if await self._evidence_stale(session, tenant, evidence):
            return False
        row.last_inference_count = expected_count
        if accepted and meaning:
            row.meaning = meaning
            row.status = "active"
        elif not row.meaning:
            row.status = "rejected"
        return True

    @with_session
    async def mark_jargon_used(
        self, session: AsyncSession, tenant: str, terms: list[str], *, tenants: list[str] | None = None
    ) -> None:
        """记录被注入的黑话词条；共享租户一起计数。"""
        if not terms:
            return
        scope = list(dict.fromkeys(tenants or [tenant]))
        await session.execute(
            update(ChatJargon)
            .where(ChatJargon.tenant.in_(scope), ChatJargon.term.in_(terms))
            .values(use_count=ChatJargon.use_count + 1, last_used_at=utc_now_naive())
        )

    @with_session
    async def delete_style(self, session: AsyncSession, tenant: str) -> bool:
        """删除租户的群风格。"""
        result = await session.execute(delete(ChatStyle).where(ChatStyle.tenant == tenant))
        return bool(result.rowcount)

    @with_session
    async def set_style_override(self, session: AsyncSession, tenant: str, text: str) -> ChatStyle:
        """保存人工风格覆盖；空文本表示取消覆盖，并让在途学习结果作废。"""
        rows = await ChatStyle.select_rows(tenant=tenant, session=session)
        now = utc_now_naive()
        if rows:
            style = rows[0]
            style.manual_override = str(text or "").strip()
            style.manual_version += 1
            style.updated_at = now
            return style
        style = ChatStyle(tenant=tenant, manual_override=str(text or "").strip(), manual_version=1, updated_at=now)
        session.add(style)
        await session.flush()
        return style

    @with_session
    async def delete_jargon(self, session: AsyncSession, tenant: str, jargon_id: int) -> bool:
        """按租户删除一条黑话释义。"""
        result = await session.execute(
            delete(ChatJargon).where(ChatJargon.tenant == tenant, ChatJargon.id == int(jargon_id))
        )
        return bool(result.rowcount)

    @with_session
    async def list_expressions(
        self,
        session: AsyncSession,
        tenant: str,
        *,
        status: str | None = None,
        limit: int = 500,
        tenants: list[str] | None = None,
        manual_only: bool = False,
    ) -> list[ChatExpression]:
        """按出现次数读取表达；tenants 合并共享租户，manual_only 只留人工确认项。"""
        scope = list(dict.fromkeys(tenants or [tenant]))
        statement = select(ChatExpression).where(ChatExpression.tenant.in_(scope))
        if status == "active":
            statement = statement.where(ChatExpression.checked.is_(True), ChatExpression.rejected.is_(False))
        elif status == "rejected":
            statement = statement.where(ChatExpression.rejected.is_(True))
        elif status == "unchecked":
            statement = statement.where(ChatExpression.checked.is_(False))
        if manual_only:
            statement = statement.where(ChatExpression.manual_checked.is_(True))
        statement = statement.order_by(ChatExpression.count.desc(), ChatExpression.updated_at.desc()).limit(limit)
        return list((await session.execute(statement)).scalars().all())

    @with_session
    async def save_expressions(
        self,
        session: AsyncSession,
        tenant: str,
        new_items: list[dict[str, object]],
        updates: dict[int, tuple[float, int, float]],
        *,
        cursor_seq: int,
        expected_cursor: int,
        source_batch_id: str,
        evidence_ids: tuple[int, ...] = (),
    ) -> bool:
        """在同一事务内写入表达、累加计数并推进游标；游标已变或证据被撤回则放弃。"""
        cursor = await self.get_cursor(tenant, "expression", session=session)
        current = int(cursor.cursor_seq) if cursor else 0
        if current != int(expected_cursor):
            return False
        if await self._evidence_stale(session, tenant, evidence_ids):
            return False
        now = utc_now_naive()
        for expression_id, (count, learned, delta) in updates.items():
            rows = await ChatExpression.select_rows(tenant=tenant, id=int(expression_id), session=session)
            if not rows:
                continue
            row = rows[0]
            row.count = float(count)
            row.learn_count += int(learned)
            row.contributions = _add_contribution(row.contributions, source_batch_id, float(delta), int(learned))
            row.updated_at = now
        for item in new_items:
            session.add(ChatExpression(tenant=tenant, source_batch_id=source_batch_id, updated_at=now, **item))
        await self._upsert_cursor(session, tenant, "expression", int(cursor_seq))
        return True

    @with_session
    async def review_expression(self, session: AsyncSession, tenant: str, expression_id: int, rejected: bool) -> bool:
        """人工改判一条表达；记录为已复审。"""
        rows = await ChatExpression.select_rows(tenant=tenant, id=int(expression_id), session=session)
        if not rows:
            return False
        rows[0].checked = True
        rows[0].manual_checked = True
        rows[0].rejected = bool(rejected)
        rows[0].updated_at = utc_now_naive()
        return True

    @with_session
    async def mark_expressions_used(
        self, session: AsyncSession, tenant: str, expression_ids: list[int], *, tenants: list[str] | None = None
    ) -> None:
        """记录实际进入注入的表达；共享租户一起计数。"""
        ids = [int(item) for item in expression_ids if int(item) > 0]
        if not ids:
            return
        scope = list(dict.fromkeys(tenants or [tenant]))
        await session.execute(
            update(ChatExpression)
            .where(ChatExpression.tenant.in_(scope), ChatExpression.id.in_(ids))
            .values(use_count=ChatExpression.use_count + 1, last_used_at=utc_now_naive())
        )

    @with_session
    async def delete_expression(self, session: AsyncSession, tenant: str, expression_id: int) -> bool:
        """按租户删除一条表达。"""
        result = await session.execute(
            delete(ChatExpression).where(ChatExpression.tenant == tenant, ChatExpression.id == int(expression_id))
        )
        return bool(result.rowcount)

    @with_session
    async def save_planner_sample(self, session: AsyncSession, sample: ChatPlannerSample) -> None:
        """一轮只保留一条规划样本；重复写入不覆盖首轮标签。"""
        values = sample.model_dump(exclude={"id"})
        statement = (
            sqlite_insert(ChatPlannerSample)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["tenant", "turn_id"])
        )
        await session.execute(statement)

    @with_session
    async def list_planner_samples(
        self, session: AsyncSession, tenant: str, limit: int = 100
    ) -> list[ChatPlannerSample]:
        """按租户读取最近的规划样本，供控制台追溯。"""
        statement = (
            select(ChatPlannerSample)
            .where(ChatPlannerSample.tenant == tenant)
            .order_by(ChatPlannerSample.id.desc())
            .limit(max(1, int(limit)))
        )
        return list((await session.execute(statement)).scalars().all())

    @with_session
    async def list_emoji(
        self,
        session: AsyncSession,
        tenant: str,
        *,
        enabled_only: bool = False,
        registered_only: bool = False,
        status: str = "",
        search: str = "",
        file_format: str = "",
        sort: str = "created_at",
        descending: bool = True,
    ) -> list[ChatEmoji]:
        """读取租户的表情包索引，可按状态、关键字筛选。"""
        statement = select(ChatEmoji).where(ChatEmoji.tenant == tenant)
        if enabled_only:
            statement = statement.where(ChatEmoji.enabled.is_(True))
        if registered_only:
            statement = statement.where(ChatEmoji.registered.is_(True), ChatEmoji.enabled.is_(True))
        if status == "registered":
            statement = statement.where(ChatEmoji.enabled.is_(True), ChatEmoji.registered.is_(True))
        elif status == "pending":
            statement = statement.where(
                ChatEmoji.enabled.is_(True), ChatEmoji.registered.is_(False), ChatEmoji.vlm_processed.is_(True)
            )
        elif status == "unidentified":
            statement = statement.where(
                ChatEmoji.enabled.is_(True), ChatEmoji.registered.is_(False), ChatEmoji.vlm_processed.is_(False)
            )
        elif status == "disabled":
            statement = statement.where(ChatEmoji.enabled.is_(False))
        keyword = str(search or "").strip()
        if keyword:
            statement = statement.where(
                (ChatEmoji.description.contains(keyword))
                | (ChatEmoji.tags.contains(keyword))
                | (ChatEmoji.hash.contains(keyword))
            )
        image_format = str(file_format or "").strip().lower().lstrip(".")
        if image_format:
            statement = statement.where(ChatEmoji.path.like(f"%.{image_format}"))
        columns = {
            "count": ChatEmoji.count,
            "last_used": ChatEmoji.last_used,
            "register_time": ChatEmoji.register_time,
        }
        column = columns.get(sort, ChatEmoji.created_at)
        statement = statement.order_by(column.desc() if descending else column.asc())
        return list((await session.execute(statement)).scalars().all())

    @with_session
    async def get_emoji(self, session: AsyncSession, tenant: str, emoji_hash: str) -> ChatEmoji | None:
        """按租户与内容哈希读取表情。"""
        rows = await ChatEmoji.select_rows(tenant=tenant, hash=emoji_hash, session=session)
        return rows[0] if rows else None

    @with_session
    async def save_emoji(
        self,
        session: AsyncSession,
        tenant: str,
        emoji_hash: str,
        path: str,
        description: str = "",
        tags: list[str] | None = None,
    ) -> ChatEmoji:
        """写入或更新一条表情索引；重复上传只刷新描述与标签。"""
        # 没有描述的表情不会进入发送候选，保持未识别状态等人工补充
        ready = bool(description.strip())
        await session.execute(
            sqlite_insert(ChatEmoji)
            .values(
                tenant=tenant,
                hash=emoji_hash,
                path=path,
                description=description,
                tags=json.dumps(tags or [], ensure_ascii=False),
                registered=ready,
                vlm_processed=ready,
                register_time=utc_now_naive() if ready else None,
            )
            .on_conflict_do_update(
                index_elements=["tenant", "hash"],
                set_={
                    "path": path,
                    "description": description,
                    "tags": json.dumps(tags or [], ensure_ascii=False),
                    "enabled": True,
                    "registered": ready,
                    "vlm_processed": ready,
                },
            )
        )
        return await self.get_emoji(tenant, emoji_hash, session=session)

    @with_session
    async def save_collected_emoji(self, session: AsyncSession, tenant: str, emoji_hash: str, path: str) -> ChatEmoji:
        """记录从聊天采集到的表情，等待识别与审核。"""
        await session.execute(
            sqlite_insert(ChatEmoji)
            .values(
                tenant=tenant,
                hash=emoji_hash,
                path=path,
                registered=False,
                vlm_processed=False,
            )
            .on_conflict_do_update(index_elements=["tenant", "hash"], set_={"path": path})
        )
        return await self.get_emoji(tenant, emoji_hash, session=session)

    @with_session
    async def set_emoji_analysis(
        self,
        session: AsyncSession,
        tenant: str,
        emoji_hash: str,
        *,
        description: str,
        tags: list[str],
        passed: bool,
    ) -> ChatEmoji | None:
        """写入识别结果；通过审核时顺带注册。"""
        values: dict[str, object] = {
            "description": description,
            "tags": json.dumps(tags, ensure_ascii=False),
            "vlm_processed": True,
        }
        if passed:
            values["registered"] = True
            values["register_time"] = utc_now_naive()
        await session.execute(
            update(ChatEmoji).where(ChatEmoji.tenant == tenant, ChatEmoji.hash == emoji_hash).values(**values)
        )
        return await self.get_emoji(tenant, emoji_hash, session=session)

    @with_session
    async def set_emoji_registered(self, session: AsyncSession, tenant: str, emoji_hash: str, registered: bool) -> bool:
        """手动注册或取消注册；取消后保留描述供审核。"""
        values: dict[str, object] = {"registered": bool(registered), "vlm_processed": True}
        if registered:
            values["register_time"] = utc_now_naive()
        result = await session.execute(
            update(ChatEmoji).where(ChatEmoji.tenant == tenant, ChatEmoji.hash == emoji_hash).values(**values)
        )
        return bool(result.rowcount)

    @with_session
    async def update_emoji_details(
        self,
        session: AsyncSession,
        tenant: str,
        emoji_hash: str,
        *,
        description: str | None = None,
        tags: list[str] | None = None,
    ) -> ChatEmoji | None:
        """控制台修改描述与标签。"""
        values: dict[str, object] = {}
        if description is not None:
            values["description"] = description
        if tags is not None:
            values["tags"] = json.dumps(tags, ensure_ascii=False)
        if values:
            await session.execute(
                update(ChatEmoji).where(ChatEmoji.tenant == tenant, ChatEmoji.hash == emoji_hash).values(**values)
            )
        return await self.get_emoji(tenant, emoji_hash, session=session)

    @with_session
    async def count_emoji(self, session: AsyncSession, tenant: str) -> dict[str, int]:
        """返回各状态数量，供控制台统计。"""
        rows = (
            await session.execute(
                select(
                    ChatEmoji.enabled,
                    ChatEmoji.registered,
                    ChatEmoji.vlm_processed,
                    func.count(),
                )
                .where(ChatEmoji.tenant == tenant)
                .group_by(ChatEmoji.enabled, ChatEmoji.registered, ChatEmoji.vlm_processed)
            )
        ).all()
        counts = {"registered": 0, "pending": 0, "unidentified": 0, "disabled": 0, "all": 0}
        for enabled, registered, processed, total in rows:
            counts["all"] += int(total)
            if not enabled:
                counts["disabled"] += int(total)
            elif registered:
                counts["registered"] += int(total)
            elif processed:
                counts["pending"] += int(total)
            else:
                counts["unidentified"] += int(total)
        return counts

    @with_session
    async def list_unregistered_emoji(self, session: AsyncSession, *, older_than: datetime) -> list[ChatEmoji]:
        """清理任务：读取超过保留期且未注册的表情。"""
        statement = select(ChatEmoji).where(ChatEmoji.registered.is_(False), ChatEmoji.created_at < older_than)
        return list((await session.execute(statement)).scalars().all())

    @with_session
    async def list_unanalyzed_emoji(self, session: AsyncSession) -> list[ChatEmoji]:
        """清理任务：读取还没识别成功的表情，供定期补识别。"""
        statement = (
            select(ChatEmoji)
            .where(
                ChatEmoji.enabled.is_(True),
                ChatEmoji.registered.is_(False),
                ChatEmoji.vlm_processed.is_(False),
            )
            .order_by(ChatEmoji.created_at.asc())
        )
        return list((await session.execute(statement)).scalars().all())

    @with_session
    async def delete_emoji_row(self, session: AsyncSession, tenant: str, emoji_hash: str) -> None:
        """只删索引，不查原行。"""
        await session.execute(delete(ChatEmoji).where(ChatEmoji.tenant == tenant, ChatEmoji.hash == emoji_hash))

    @with_session
    async def mark_emoji_used(
        self, session: AsyncSession, tenant: str, emoji_hash: str, *, now: datetime | None = None
    ) -> None:
        """记录表情实际发送。"""
        await session.execute(
            update(ChatEmoji)
            .where(ChatEmoji.tenant == tenant, ChatEmoji.hash == emoji_hash)
            .values(count=ChatEmoji.count + 1, last_used=now or utc_now_naive())
        )

    @with_session
    async def set_emoji_enabled(self, session: AsyncSession, tenant: str, emoji_hash: str, enabled: bool) -> bool:
        """启停一条表情。"""
        result = await session.execute(
            update(ChatEmoji)
            .where(ChatEmoji.tenant == tenant, ChatEmoji.hash == emoji_hash)
            .values(enabled=bool(enabled))
        )
        return bool(result.rowcount)

    @with_session
    async def delete_emoji(self, session: AsyncSession, tenant: str, emoji_hash: str) -> ChatEmoji | None:
        """删除索引并返回原行，供调用方清理文件。"""
        row = await self.get_emoji(tenant, emoji_hash, session=session)
        if row is not None:
            await session.execute(delete(ChatEmoji).where(ChatEmoji.tenant == tenant, ChatEmoji.hash == emoji_hash))
        return row

    @with_session
    async def save_trace(
        self,
        session: AsyncSession,
        tenant: str,
        *,
        turn_id: str,
        trigger_kind: str,
        request_preview: str,
        injected: str,
        budget: str,
        provider: str,
        latency_ms: int,
        error: str = "",
    ) -> None:
        """同一逻辑轮次更新同一条追踪，阶段 JSON 保持完整。"""
        statement = select(ChatTrace).where(ChatTrace.tenant == tenant, ChatTrace.turn_id == turn_id)
        row = (await session.execute(statement)).scalars().first()
        if row is None:
            row = ChatTrace(tenant=tenant, turn_id=str(turn_id or ""))
            session.add(row)
        row.trigger_kind = str(trigger_kind or "")
        row.request_preview = str(request_preview or "")[:200]
        row.injected = str(injected or "{}")[:4000]
        row.budget = str(budget or "{}")
        row.provider = str(provider or "")[:100]
        row.latency_ms = max(0, int(latency_ms))
        row.error = str(error or "")[:500]

    @with_session
    async def update_trace(
        self,
        session: AsyncSession,
        tenant: str,
        turn_id: str,
        *,
        budget: dict[str, Any],
        latency_ms: int = 0,
        total_latency_ms: int | None = None,
        error: str = "",
    ) -> None:
        """合并同一租户轮次的追踪信息并累加耗时与错误。"""
        rows = await session.execute(select(ChatTrace).where(ChatTrace.tenant == tenant, ChatTrace.turn_id == turn_id))
        for row in rows.scalars():
            try:
                merged = json.loads(row.budget)
            except (TypeError, ValueError):
                merged = {}
            if not isinstance(merged, dict):
                merged = {}
            merged.update(budget)
            row.budget = json.dumps(merged, ensure_ascii=False)
            if total_latency_ms is None:
                row.latency_ms += max(0, int(latency_ms))
            else:
                row.latency_ms = max(0, int(total_latency_ms))
            if error:
                row.error = "\n".join(filter(None, (row.error, error)))

    @with_session
    async def list_traces(self, session: AsyncSession, tenant: str, limit: int = 100) -> list[ChatTrace]:
        """读取最近的轮次追踪。"""
        statement = (
            select(ChatTrace)
            .where(ChatTrace.tenant == tenant)
            .order_by(ChatTrace.created_at.desc())
            .limit(max(1, int(limit)))
        )
        return list((await session.execute(statement)).scalars().all())

    @with_session
    async def clear_traces(self, session: AsyncSession, before: datetime) -> int:
        """删除指定时间之前的追踪。"""
        result = await session.execute(delete(ChatTrace).where(ChatTrace.created_at < before))
        return int(result.rowcount or 0)

    @with_session
    async def decay_expressions(
        self,
        session: AsyncSession,
        now: datetime,
        *,
        factor: float = 0.99,
        min_count: float = 0.1,
        idle_days: int = 30,
    ) -> dict[str, int]:
        """表达计数每周衰减；长时间未用且低于阈值的条目驳回。"""
        cutoff = now - timedelta(days=max(1, idle_days))
        rows = list(
            (
                await session.execute(
                    select(ChatExpression).where(ChatExpression.checked.is_(True), ChatExpression.rejected.is_(False))
                )
            ).scalars()
        )
        counts = {"decayed": 0, "rejected": 0}
        for row in rows:
            row.count = max(0.0, float(row.count) * factor)
            counts["decayed"] += 1
            last_used = row.last_used_at or row.updated_at
            if row.count < min_count and last_used < cutoff:
                row.rejected = True
                counts["rejected"] += 1
        return counts

    @with_session
    async def invalidate_message(
        self, session: AsyncSession, tenant: str, platform_message_id: str, *, operator: str = ""
    ) -> dict[str, int] | None:
        """撤回消息：未落库时保留墓碑，否则标记删除并丢弃相关学习批次。"""
        if not platform_message_id:
            return None
        now = utc_now_naive()
        await session.execute(
            sqlite_insert(ChatRecallTombstone)
            .values(tenant=tenant, platform_message_id=platform_message_id, deleted_at=now, delete_operator=operator)
            .on_conflict_do_nothing(index_elements=["tenant", "platform_message_id"])
        )
        rows = await ChatMessage.select_rows(tenant=tenant, platform_message_id=platform_message_id, session=session)
        if not rows:
            return None
        message = rows[0]
        await session.execute(
            update(ChatMessage)
            .where(ChatMessage.tenant == tenant, ChatMessage.id == message.id)
            .values(deleted=True, deleted_at=now, delete_operator=operator)
        )
        await session.execute(
            delete(ChatRecallTombstone).where(
                ChatRecallTombstone.tenant == tenant, ChatRecallTombstone.platform_message_id == platform_message_id
            )
        )
        styles = list((await session.execute(select(ChatStyle).where(ChatStyle.tenant == tenant))).scalars())
        for style in styles:
            if not int(style.evidence_seq_start) <= int(message.id) <= int(style.evidence_seq_end):
                continue
            # 摘要与规则由整批证据生成，无法逐条剥离：清空派生内容，下一轮从原证据窗口重建
            rebuild_from = max(0, int(style.evidence_seq_start) - 1)
            style.summary = ""
            style.rules = "[]"
            style.examples = "[]"
            style.evidence_seq_start = 0
            style.evidence_seq_end = rebuild_from
            style.evidence_count = 0
            style.needs_rebuild = True
            style.updated_at = now
            await self._upsert_cursor(session, tenant, "style", rebuild_from)
        stale_batches: list[str] = []
        jargon_rows = list((await session.execute(select(ChatJargon).where(ChatJargon.tenant == tenant))).scalars())
        for row in jargon_rows:
            batch = _batch_range(row.source_batch_id)
            if batch is not None and batch[0] == "style" and batch[1] <= int(message.id) <= batch[2]:
                stale_batches.append(row.source_batch_id)
        if stale_batches:
            await session.execute(
                delete(ChatJargon).where(
                    ChatJargon.tenant == tenant,
                    ChatJargon.source_batch_id.in_(stale_batches),
                )
            )
        stale_expression_ids: list[int] = []
        rolled_back = 0
        expression_rows = list(
            (await session.execute(select(ChatExpression).where(ChatExpression.tenant == tenant))).scalars()
        )
        for row in expression_rows:
            if int(row.source_message_seq) == int(message.id):
                stale_expression_ids.append(int(row.id or 0))
                continue
            batch = _batch_range(row.source_batch_id)
            if batch is not None and batch[0] == "expression" and batch[1] <= int(message.id) <= batch[2]:
                stale_expression_ids.append(int(row.id or 0))
                continue
            kept: list[dict[str, Any]] = []
            count_back = 0.0
            learn_back = 0
            try:
                entries = json.loads(row.contributions or "[]")
            except (TypeError, ValueError):
                entries = []
            for entry in entries if isinstance(entries, list) else []:
                if not isinstance(entry, dict):
                    continue
                batch = _batch_range(entry.get("batch"))
                if batch is not None and batch[0] == "expression" and batch[1] <= int(message.id) <= batch[2]:
                    count_back += float(entry.get("count") or 0)
                    learn_back += int(entry.get("learn") or 0)
                else:
                    kept.append(entry)
            if count_back or learn_back:
                row.contributions = json.dumps(kept, ensure_ascii=False)
                row.count = max(0.0, float(row.count) - count_back)
                row.learn_count = max(0, int(row.learn_count) - learn_back)
                row.updated_at = now
                rolled_back += 1
        if stale_expression_ids:
            await session.execute(
                delete(ChatExpression).where(
                    ChatExpression.tenant == tenant,
                    ChatExpression.id.in_(stale_expression_ids),
                )
            )
        return {
            "jargon_batches": len(stale_batches),
            "expressions": len(stale_expression_ids),
            "rolled_back": rolled_back,
        }

    async def _state_row(self, session: AsyncSession, tenant: str) -> ChatSessionState:
        """读取或新建会话状态行，调用方事务内使用。"""
        rows = await ChatSessionState.select_rows(tenant=tenant, session=session)
        if rows:
            return rows[0]
        state = ChatSessionState(tenant=tenant)
        session.add(state)
        await session.flush()
        return state

    async def _upsert_cursor(
        self,
        session: AsyncSession,
        tenant: str,
        job: str,
        cursor_seq: int,
        *,
        direction: str = "forward",
        stop_seq: int | None = None,
        fail_count: int = 0,
        next_retry_at: datetime | None = None,
        last_error: str = "",
    ) -> None:
        """写入游标；成功时清空退避状态。"""
        now = utc_now_naive()
        updates: dict[str, object] = {
            "direction": direction,
            "cursor_seq": int(cursor_seq),
            "fail_count": fail_count,
            "next_retry_at": next_retry_at,
            "last_error": last_error,
            "updated_at": now,
        }
        if stop_seq is not None:
            updates["stop_seq"] = int(stop_seq)
        statement = (
            sqlite_insert(ChatJobCursor)
            .values(
                tenant=tenant,
                job=job,
                direction=direction,
                cursor_seq=int(cursor_seq),
                stop_seq=int(stop_seq or 0),
                fail_count=fail_count,
                next_retry_at=next_retry_at,
                last_error=last_error,
                updated_at=now,
            )
            .on_conflict_do_update(index_elements=["tenant", "job"], set_=updates)
        )
        await session.execute(statement)


chat_repository = ChatRepository()
