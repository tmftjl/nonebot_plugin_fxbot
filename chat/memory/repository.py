"""记忆历史的租户级持久化。"""

from __future__ import annotations

import json
import hashlib
from typing import Any
from datetime import datetime, timedelta
from dataclasses import dataclass

from sqlmodel import select
from sqlalchemy import func, delete, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ...db import with_session
from .models import (
    ChatTurn,
    ChatAlias,
    ChatState,
    ChatStyle,
    ChatJargon,
    ChatMemory,
    ChatEpisode,
    ChatMessage,
    ChatProfile,
    ChatBehavior,
    ChatJobBatch,
    ChatRelation,
    ChatTurnItem,
    ChatJobCursor,
    ChatExpression,
    ChatForgetMarker,
    ChatSessionState,
    ChatPlannerSample,
    ChatBehaviorSample,
    ChatMemoryEvidence,
    utc_now_naive,
)
from ...config import get_manager as get_config_manager
from .filtering import should_learn
from ...utils.tz import as_shanghai

FAMILIARITY_STEP = 0.02
BEHAVIOR_OBSERVE_MINUTES = 10
BEHAVIOR_MIN_SAMPLES = 50


def _mood_config() -> dict[str, Any]:
    """读取情绪配置；缺失或非法时按默认值处理。"""
    try:
        value = get_config_manager().get_system()["chat"].get("mood")
    except Exception:
        value = None
    return value if isinstance(value, dict) else {}


def mood_enabled() -> bool:
    """情绪与熟悉度是否启用。"""
    return bool(_mood_config().get("enabled", True))


def _mood_decay_per_day() -> float:
    value = _mood_config().get("decay_per_day", 0.1)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.1
    return min(1.0, max(0.0, float(value)))


def _toward_zero(value: float, amount: float) -> float:
    return max(0.0, value - amount) if value >= 0 else min(0.0, value + amount)


def _clamp(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))


@dataclass(frozen=True)
class TenantJobState:
    """调度所需的租户消息与游标快照。"""

    tenant: str
    last_message_id: int
    message_count: int
    learn_enabled: bool | None
    memory: ChatJobCursor | None
    backfill: ChatJobCursor | None
    backfilled_at: datetime | None
    backfill_done: int = 0


class ChatRepository:
    """只暴露按 tenant 隔离的读写，不跨租户查询。"""

    @with_session
    async def append_message(self, session: AsyncSession, message: ChatMessage) -> int:
        """按 dedupe_key 幂等写入，已存在时保留原行；同时记录发言人昵称。"""
        values = message.model_dump(exclude={"id"})
        statement = (
            sqlite_insert(ChatMessage).values(**values).on_conflict_do_nothing(index_elements=["tenant", "dedupe_key"])
        )
        await session.execute(statement)
        if message.kind == "user":
            await self._write_alias(session, message.tenant, message.sender_id, message.sender_name)
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
        """发送成功后让助手条目进入历史；全部段发完才把轮次标为已发送。"""
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
        items = await ChatTurnItem.select_rows(tenant=tenant, turn_id=item.turn_id, session=session)
        assistant = [row for row in items if row.role == "assistant"]
        if assistant and all(row.id == item_id or row.send_status == "sent" for row in assistant):
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
    async def get_states(
        self, session: AsyncSession, tenant: str, subject_id: str
    ) -> tuple[ChatState | None, ChatState | None]:
        """读取租户情绪行与当前用户的熟悉度行，并顺带应用每日衰减。"""
        if not mood_enabled():
            return None, None
        now = utc_now_naive()
        decay = _mood_decay_per_day()
        mood = await self._chat_state_row(session, tenant, "", create=False)
        user = await self._chat_state_row(session, tenant, subject_id, create=False) if subject_id else None
        for row in (mood, user):
            if row is not None:
                self._decay_state(row, now, decay)
        return mood, user

    @with_session
    async def touch_interaction(
        self, session: AsyncSession, tenant: str, subject_id: str, *, now: datetime | None = None
    ) -> None:
        """每次互动提升熟悉度；跨天后先衰减一次。"""
        if not subject_id or not mood_enabled():
            return
        timestamp = now or utc_now_naive()
        row = await self._chat_state_row(session, tenant, subject_id)
        self._decay_state(row, timestamp, _mood_decay_per_day())
        row.familiarity = min(1.0, row.familiarity + FAMILIARITY_STEP)
        row.last_interaction_at = timestamp
        row.updated_at = timestamp

    @with_session
    async def apply_emotion(
        self,
        session: AsyncSession,
        tenant: str,
        valence: float,
        arousal: float,
        *,
        now: datetime | None = None,
    ) -> None:
        """按批次情绪更新租户 EMA；同一批次只调用一次。"""
        if not mood_enabled():
            return
        await self._apply_emotion(session, tenant, (valence, arousal), now or utc_now_naive())

    @with_session
    async def upsert_alias(
        self, session: AsyncSession, tenant: str, user_id: str, alias: str, *, source: str = "nickname"
    ) -> None:
        """记录发言人昵称；同名多用户保留多行。"""
        await self._write_alias(session, tenant, user_id, alias, source)

    @with_session
    async def get_aliases(self, session: AsyncSession, tenant: str, user_ids: list[str]) -> dict[str, list[str]]:
        """返回指定成员的最近别名。"""
        if not user_ids:
            return {}
        statement = (
            select(ChatAlias)
            .where(ChatAlias.tenant == tenant, ChatAlias.user_id.in_(user_ids))
            .order_by(ChatAlias.last_seen.desc())
        )
        rows = (await session.execute(statement)).scalars().all()
        result: dict[str, list[str]] = {}
        for row in rows:
            result.setdefault(row.user_id, []).append(row.alias)
        return result

    @with_session
    async def get_cursor(self, session: AsyncSession, tenant: str, job: str = "memory") -> ChatJobCursor | None:
        """读取某类任务的实时游标。"""
        rows = await ChatJobCursor.select_rows(tenant=tenant, job=job, session=session)
        return rows[0] if rows else None

    @with_session
    async def scheduling_snapshot(self, session: AsyncSession) -> list[TenantJobState]:
        """一次读出所有有消息租户的游标、学习开关与回填状态。"""
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
        cursors: dict[tuple[str, str], ChatJobCursor] = {
            (str(row.tenant), row.job): row
            for row in (await session.execute(select(ChatJobCursor).where(ChatJobCursor.tenant.in_(tenants))))
            .scalars()
            .all()
        }
        done_rows = (
            await session.execute(
                select(ChatJobBatch.tenant, func.count())
                .where(
                    ChatJobBatch.tenant.in_(tenants),
                    ChatJobBatch.job == "backfill",
                    ChatJobBatch.status == "done",
                )
                .group_by(ChatJobBatch.tenant)
            )
        ).all()
        done = {str(row[0]): int(row[1]) for row in done_rows}
        return [
            TenantJobState(
                tenant=tenant,
                last_message_id=last_id,
                message_count=count,
                learn_enabled=states[tenant].learn_enabled if tenant in states else None,
                memory=cursors.get((tenant, "memory")),
                backfill=cursors.get((tenant, "backfill")),
                backfilled_at=states[tenant].backfilled_at if tenant in states else None,
                backfill_done=done.get(tenant, 0),
            )
            for tenant, (last_id, count) in last_ids.items()
        ]

    @with_session
    async def list_memory_tenants(self, session: AsyncSession) -> list[dict[str, object]]:
        """返回有消息、群风格或黑话的租户及消息数，供控制台选择。"""
        count_rows = (
            await session.execute(
                select(ChatMessage.tenant, func.count(ChatMessage.id))
                .where(ChatMessage.kind == "user", ChatMessage.deleted.is_(False))
                .group_by(ChatMessage.tenant)
            )
        ).all()
        counts = {str(row[0]): int(row[1]) for row in count_rows}
        style_tenants = {str(row[0]) for row in (await session.execute(select(ChatStyle.tenant))).all()}
        jargon_tenants = {str(row[0]) for row in (await session.execute(select(ChatJargon.tenant))).all()}
        expression_tenants = {str(row[0]) for row in (await session.execute(select(ChatExpression.tenant))).all()}
        tenants = sorted(set(counts) | style_tenants | jargon_tenants | expression_tenants)
        return [
            {"tenant": tenant, "messages": counts.get(tenant, 0)}
            for tenant in sorted(tenants, key=lambda item: (-counts.get(item, 0), item))
        ]

    @with_session
    async def ensure_backfill(
        self,
        session: AsyncSession,
        tenant: str,
        *,
        min_messages: int,
        max_messages: int,
    ) -> bool:
        """首次学习时冻结回填窗口，并让实时游标跳过这段旧消息。"""
        state = await self._state_row(session, tenant)
        if state.backfilled_at is not None:
            return False
        rows = await ChatJobCursor.select_rows(tenant=tenant, job="backfill", session=session)
        if rows:
            return False
        total = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(ChatMessage)
                    .where(
                        ChatMessage.tenant == tenant,
                        ChatMessage.kind == "user",
                        ChatMessage.deleted.is_(False),
                    )
                )
            ).scalar_one()
        )
        if total < max(1, int(min_messages)):
            return False
        boundary = int(
            (
                await session.execute(
                    select(func.max(ChatMessage.id)).where(
                        ChatMessage.tenant == tenant,
                        ChatMessage.kind == "user",
                        ChatMessage.deleted.is_(False),
                    )
                )
            ).scalar_one()
            or 0
        )
        if boundary <= 0:
            return False
        recent = list(
            (
                await session.execute(
                    select(ChatMessage)
                    .where(
                        ChatMessage.tenant == tenant,
                        ChatMessage.kind == "user",
                        ChatMessage.deleted.is_(False),
                        ChatMessage.id <= boundary,
                    )
                    .order_by(ChatMessage.id.desc())
                    .limit(max(int(max_messages) * 4, int(min_messages)))
                )
            )
            .scalars()
            .all()
        )
        clean_ids: list[int] = []
        for message in recent:
            if message.id is None:
                continue
            content = " ".join(str(message.content or "").split())
            if should_learn(content, tenant=tenant, check_duplicate=False):
                clean_ids.append(int(message.id))
                if len(clean_ids) >= int(max_messages):
                    break
        if not clean_ids:
            state.backfilled_at = utc_now_naive()
            state.updated_at = state.backfilled_at
            return False
        stop_seq = min(clean_ids) - 1
        memory = await self.get_cursor(tenant, "memory", session=session)
        if memory is None or int(memory.cursor_seq) < boundary:
            await self._upsert_cursor(session, tenant, "memory", boundary, direction="forward")
        timestamp = utc_now_naive()
        await session.execute(
            sqlite_insert(ChatJobCursor)
            .values(
                tenant=tenant,
                job="backfill",
                direction="backward",
                cursor_seq=boundary + 1,
                stop_seq=stop_seq,
                fail_count=0,
                updated_at=timestamp,
            )
            .on_conflict_do_nothing(index_elements=["tenant", "job"])
        )
        state.updated_at = timestamp
        return True

    @with_session
    async def done_batch_count(self, session: AsyncSession, tenant: str, job: str, *, status: str = "done") -> int:
        """统计某类任务已完成的批次数。"""
        statement = (
            select(func.count())
            .select_from(ChatJobBatch)
            .where(
                ChatJobBatch.tenant == tenant,
                ChatJobBatch.job == job,
                ChatJobBatch.status == status,
            )
        )
        return int((await session.execute(statement)).scalar_one())

    @with_session
    async def finish_backfill(self, session: AsyncSession, tenant: str) -> None:
        """回填完成写 backfilled_at，之后不再重新扫描历史。"""
        timestamp = utc_now_naive()
        state = await self._state_row(session, tenant)
        state.backfilled_at = timestamp
        state.updated_at = timestamp
        await session.execute(
            update(ChatJobCursor)
            .where(ChatJobCursor.tenant == tenant, ChatJobCursor.job == "backfill")
            .values(fail_count=0, next_retry_at=None, last_error="", updated_at=timestamp)
        )

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
    async def messages_between(
        self,
        session: AsyncSession,
        tenant: str,
        after_id: int,
        before_id: int,
        limit: int = 200,
    ) -> list[ChatMessage]:
        """按主键倒序读取一段人类消息，用于从新到旧回填。"""
        statement = (
            select(ChatMessage)
            .where(
                ChatMessage.tenant == tenant,
                ChatMessage.kind == "user",
                ChatMessage.deleted.is_(False),
                ChatMessage.id > after_id,
                ChatMessage.id < before_id,
            )
            .order_by(ChatMessage.id.desc())
            .limit(limit)
        )
        return list((await session.execute(statement)).scalars().all())

    @with_session
    async def get_facts(self, session: AsyncSession, tenant: str, subject_id: str, limit: int = 10) -> list[ChatMemory]:
        """读取主体仍有效且证据未失效的事实。"""
        statement = (
            select(ChatMemory)
            .where(
                ChatMemory.tenant == tenant,
                ChatMemory.subject_id == subject_id,
                ChatMemory.status == "active",
                ChatMemory.valid_evidence_count > 0,
            )
            .order_by(ChatMemory.importance.desc(), ChatMemory.confidence.desc(), ChatMemory.updated_at.desc())
            .limit(limit)
        )
        return list((await session.execute(statement)).scalars().all())

    @with_session
    async def get_recent_episodes(self, session: AsyncSession, tenant: str, limit: int = 2) -> list[ChatEpisode]:
        """读取最近仍可用的 Episode 摘要。"""
        statement = (
            select(ChatEpisode)
            .where(ChatEpisode.tenant == tenant, ChatEpisode.status == "active")
            .order_by(ChatEpisode.id.desc())
            .limit(limit)
        )
        return list((await session.execute(statement)).scalars().all())

    @with_session
    async def list_active_episodes(self, session: AsyncSession, tenant: str, limit: int = 10000) -> list[ChatEpisode]:
        """读取检索索引需要的可用 Episode。"""
        statement = (
            select(ChatEpisode)
            .where(ChatEpisode.tenant == tenant, ChatEpisode.status == "active")
            .order_by(ChatEpisode.id.desc())
            .limit(limit)
        )
        return list((await session.execute(statement)).scalars().all())

    @with_session
    async def list_active_facts(self, session: AsyncSession, tenant: str, limit: int = 10000) -> list[ChatMemory]:
        """读取检索索引需要的事实，只保留证据仍有效的行。"""
        statement = (
            select(ChatMemory)
            .where(
                ChatMemory.tenant == tenant,
                ChatMemory.status == "active",
                ChatMemory.valid_evidence_count > 0,
            )
            .order_by(ChatMemory.id.desc())
            .limit(limit)
        )
        return list((await session.execute(statement)).scalars().all())

    @with_session
    async def get_facts_for_subjects(
        self, session: AsyncSession, tenant: str, subject_ids: list[str], limit: int = 60
    ) -> list[ChatMemory]:
        """一次读出多个主体的有效事实，用于关系扩展。"""
        if not subject_ids:
            return []
        statement = (
            select(ChatMemory)
            .where(
                ChatMemory.tenant == tenant,
                ChatMemory.subject_id.in_(subject_ids),
                ChatMemory.status == "active",
                ChatMemory.valid_evidence_count > 0,
            )
            .order_by(ChatMemory.importance.desc(), ChatMemory.confidence.desc(), ChatMemory.updated_at.desc())
            .limit(limit)
        )
        return list((await session.execute(statement)).scalars().all())

    @with_session
    async def get_relations(
        self, session: AsyncSession, tenant: str, subject_ids: list[str], limit: int = 200
    ) -> list[ChatRelation]:
        """读取这些主体出发的仍有效关系边。"""
        if not subject_ids:
            return []
        statement = (
            select(ChatRelation)
            .where(
                ChatRelation.tenant == tenant,
                ChatRelation.subject_id.in_(subject_ids),
                ChatRelation.status == "active",
            )
            .order_by(ChatRelation.confidence.desc(), ChatRelation.id.desc())
            .limit(limit)
        )
        return list((await session.execute(statement)).scalars().all())

    @with_session
    async def get_style(self, session: AsyncSession, tenant: str) -> ChatStyle | None:
        """读取租户的群风格；不存在时返回 None。"""
        rows = await ChatStyle.select_rows(tenant=tenant, session=session)
        return rows[0] if rows else None

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
    ) -> bool:
        """写入风格与黑话；人工覆盖版本变化时放弃本次结果。"""
        from .style import StyleResult

        if not isinstance(result, StyleResult):
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
            style.version += 1
            style.learn_count += 1
            style.updated_at = now
        else:
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
    async def list_jargon(
        self, session: AsyncSession, tenant: str, *, status: str | None = None, limit: int = 500
    ) -> list[ChatJargon]:
        """按出现次数读取黑话条目。"""
        statement = select(ChatJargon).where(ChatJargon.tenant == tenant)
        if status:
            statement = statement.where(ChatJargon.status == status)
        statement = statement.order_by(ChatJargon.count.desc(), ChatJargon.last_seen.desc()).limit(limit)
        return list((await session.execute(statement)).scalars().all())

    @with_session
    async def mark_jargon_used(self, session: AsyncSession, tenant: str, terms: list[str]) -> None:
        """记录被注入的黑话词条。"""
        if not terms:
            return
        await session.execute(
            update(ChatJargon)
            .where(ChatJargon.tenant == tenant, ChatJargon.term.in_(terms))
            .values(use_count=ChatJargon.use_count + 1, last_used_at=utc_now_naive())
        )

    @with_session
    async def delete_style(self, session: AsyncSession, tenant: str) -> bool:
        """删除租户的群风格。"""
        result = await session.execute(delete(ChatStyle).where(ChatStyle.tenant == tenant))
        return bool(result.rowcount)

    @with_session
    async def delete_jargon(self, session: AsyncSession, tenant: str, jargon_id: int) -> bool:
        """按租户删除一条黑话释义。"""
        result = await session.execute(
            delete(ChatJargon).where(ChatJargon.tenant == tenant, ChatJargon.id == int(jargon_id))
        )
        return bool(result.rowcount)

    @with_session
    async def list_expressions(
        self, session: AsyncSession, tenant: str, *, status: str | None = None, limit: int = 500
    ) -> list[ChatExpression]:
        """按出现次数读取表达；status 支持 active/rejected/unchecked。"""
        statement = select(ChatExpression).where(ChatExpression.tenant == tenant)
        if status == "active":
            statement = statement.where(ChatExpression.checked.is_(True), ChatExpression.rejected.is_(False))
        elif status == "rejected":
            statement = statement.where(ChatExpression.rejected.is_(True))
        elif status == "unchecked":
            statement = statement.where(ChatExpression.checked.is_(False))
        statement = statement.order_by(ChatExpression.count.desc(), ChatExpression.updated_at.desc()).limit(limit)
        return list((await session.execute(statement)).scalars().all())

    @with_session
    async def save_expressions(
        self,
        session: AsyncSession,
        tenant: str,
        new_items: list[dict[str, object]],
        updates: dict[int, tuple[float, int]],
        *,
        cursor_seq: int,
        expected_cursor: int,
        source_batch_id: str,
    ) -> bool:
        """在同一事务内写入表达、累加计数并推进游标；游标已变则放弃。"""
        cursor = await self.get_cursor(tenant, "expression", session=session)
        current = int(cursor.cursor_seq) if cursor else 0
        if current != int(expected_cursor):
            return False
        now = utc_now_naive()
        for expression_id, (count, learned) in updates.items():
            await session.execute(
                update(ChatExpression)
                .where(ChatExpression.tenant == tenant, ChatExpression.id == int(expression_id))
                .values(
                    count=float(count),
                    learn_count=ChatExpression.learn_count + int(learned),
                    updated_at=now,
                )
            )
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
        rows[0].rejected = bool(rejected)
        rows[0].updated_at = utc_now_naive()
        return True

    @with_session
    async def mark_expressions_used(self, session: AsyncSession, tenant: str, expression_ids: list[int]) -> None:
        """记录实际进入注入的表达。"""
        ids = [int(item) for item in expression_ids if int(item) > 0]
        if not ids:
            return
        await session.execute(
            update(ChatExpression)
            .where(ChatExpression.tenant == tenant, ChatExpression.id.in_(ids))
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
    async def record_behavior_sample(
        self,
        session: AsyncSession,
        tenant: str,
        turn_id: str,
        trigger_kind: str,
        scene_tags: list[str],
        action: str,
        *,
        now: datetime | None = None,
    ) -> None:
        """动作发生时写一条待归因样本。"""
        timestamp = now or utc_now_naive()
        statement = (
            sqlite_insert(ChatBehaviorSample)
            .values(
                tenant=tenant,
                turn_id=turn_id,
                trigger_kind=trigger_kind,
                scene_tags=json.dumps(scene_tags, ensure_ascii=False),
                action=action,
                acted_at=timestamp,
                observed_until=timestamp + timedelta(minutes=BEHAVIOR_OBSERVE_MINUTES),
                outcome="pending",
            )
            .on_conflict_do_nothing(index_elements=["tenant", "turn_id", "action"])
        )
        await session.execute(statement)

    @with_session
    async def attribute_behavior_samples(
        self, session: AsyncSession, *, now: datetime | None = None, limit: int = 500
    ) -> int:
        """观察窗口结束后，按窗口内是否有人继续发言写入结果。"""
        timestamp = now or utc_now_naive()
        statement = (
            select(ChatBehaviorSample)
            .where(
                ChatBehaviorSample.outcome == "pending",
                ChatBehaviorSample.observed_until <= timestamp,
            )
            .limit(max(1, int(limit)))
        )
        rows = list((await session.execute(statement)).scalars())
        for row in rows:
            continued = (
                await session.execute(
                    select(func.count())
                    .select_from(ChatMessage)
                    .where(
                        ChatMessage.tenant == row.tenant,
                        ChatMessage.kind == "user",
                        ChatMessage.deleted.is_(False),
                        ChatMessage.ingested_at > row.acted_at,
                        ChatMessage.ingested_at <= row.observed_until,
                    )
                )
            ).scalar_one()
            row.outcome = "continued" if continued else "ignored"
            row.attributed_at = timestamp
        return len(rows)

    @with_session
    async def behavior_tenants(self, session: AsyncSession) -> list[str]:
        """有已归因样本的租户，用于周聚合。"""
        statement = select(ChatBehaviorSample.tenant).where(ChatBehaviorSample.outcome != "pending").distinct()
        return [str(item) for item in (await session.execute(statement)).scalars()]

    @with_session
    async def aggregate_behavior(self, session: AsyncSession, tenant: str) -> int:
        """按场景与动作聚合已归因样本，覆盖该租户的旧聚合。"""
        statement = select(ChatBehaviorSample).where(
            ChatBehaviorSample.tenant == tenant,
            ChatBehaviorSample.outcome != "pending",
        )
        samples = list((await session.execute(statement)).scalars())
        groups: dict[tuple[str, str], dict[str, Any]] = {}
        for sample in samples:
            key = (sample.scene_tags, sample.action)
            group = groups.setdefault(key, {"count": 0, "continued": 0, "last_seen": sample.acted_at})
            group["count"] += 1
            group["continued"] += 1 if sample.outcome == "continued" else 0
            group["last_seen"] = max(group["last_seen"], sample.acted_at)
        await session.execute(delete(ChatBehavior).where(ChatBehavior.tenant == tenant))
        for (scene_tags, action), group in groups.items():
            count = int(group["count"])
            confidence = int(group["continued"]) / count if count else 0.0
            session.add(
                ChatBehavior(
                    tenant=tenant,
                    scene_hash=hashlib.sha1(scene_tags.encode("utf-8")).hexdigest(),
                    scene_tags=scene_tags,
                    action=action,
                    outcome="continued" if confidence >= 0.5 else "ignored",
                    confidence=confidence,
                    count=count,
                    last_seen=group["last_seen"],
                )
            )
        return len(groups)

    @with_session
    async def behavior_hints(
        self, session: AsyncSession, tenant: str, *, limit: int = 3, minimum: int = BEHAVIOR_MIN_SAMPLES
    ) -> tuple[list[ChatBehavior], int]:
        """样本足够时返回 top 场景→动作；样本数按已归因原始样本计。"""
        total = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(ChatBehaviorSample)
                    .where(
                        ChatBehaviorSample.tenant == tenant,
                        ChatBehaviorSample.outcome != "pending",
                    )
                )
            ).scalar_one()
        )
        if total < max(0, int(minimum)):
            return [], total
        statement = (
            select(ChatBehavior)
            .where(ChatBehavior.tenant == tenant)
            .order_by(ChatBehavior.confidence.desc(), ChatBehavior.count.desc())
            .limit(max(1, int(limit)))
        )
        return list((await session.execute(statement)).scalars()), total

    @with_session
    async def clear_behavior(self, session: AsyncSession, tenant: str) -> None:
        """删除租户的行为样本与聚合。"""
        await session.execute(delete(ChatBehaviorSample).where(ChatBehaviorSample.tenant == tenant))
        await session.execute(delete(ChatBehavior).where(ChatBehavior.tenant == tenant))

    @with_session
    async def get_profile(self, session: AsyncSession, tenant: str, subject_id: str) -> ChatProfile | None:
        """读取主体画像；不存在时返回 None。"""
        rows = await ChatProfile.select_rows(tenant=tenant, subject_id=subject_id, session=session)
        return rows[0] if rows else None

    @with_session
    async def mark_batch_failed(
        self,
        session: AsyncSession,
        tenant: str,
        range_start: int,
        range_end: int,
        *,
        job: str = "memory",
        algorithm_version: int = 1,
        error: str = "",
        direction: str = "forward",
    ) -> datetime:
        """记录批次失败并按 1/2/5/10/30 分钟退避。"""
        now = utc_now_naive()
        cursor = await self.get_cursor(tenant, job, session=session)
        fail_count = (cursor.fail_count if cursor else 0) + 1
        delays = (1, 2, 5, 10, 30)
        next_retry = now + timedelta(minutes=delays[min(fail_count - 1, len(delays) - 1)])
        statement = (
            sqlite_insert(ChatJobBatch)
            .values(
                tenant=tenant,
                job=job,
                range_start=range_start,
                range_end=range_end,
                algorithm_version=algorithm_version,
                status="failed",
                attempt=fail_count,
                error=error[:500],
            )
            .on_conflict_do_update(
                index_elements=["tenant", "job", "range_start", "range_end", "algorithm_version"],
                set_={"status": "failed", "attempt": fail_count, "error": error[:500]},
            )
        )
        await session.execute(statement)
        await self._upsert_cursor(
            session,
            tenant,
            job,
            cursor.cursor_seq if cursor else 0,
            direction=cursor.direction if cursor else direction,
            fail_count=fail_count,
            next_retry_at=next_retry,
            last_error=error[:500],
        )
        return next_retry

    @with_session
    async def commit_extraction(
        self,
        session: AsyncSession,
        tenant: str,
        batch_id: str,
        messages: list[ChatMessage],
        high_water: int,
        result: object,
        *,
        job: str = "memory",
        algorithm_version: int = 1,
        direction: str = "forward",
        next_cursor: int | None = None,
        source: str = "extract",
    ) -> dict[str, int]:
        """在一个事务里写 Episode、事实、证据、画像并推进游标。"""
        from .extract import ExtractionResult

        if not messages or not isinstance(result, ExtractionResult):
            return {"facts": 0, "episodes": 0, "relations": 0}
        message_by_id = {int(message.id): message for message in messages if message.id is not None}
        if not message_by_id:
            return {"facts": 0, "episodes": 0, "relations": 0}
        seq_start, seq_end = min(message_by_id), max(high_water, min(message_by_id))
        author_names = {message.sender_id: message.sender_name for message in messages if message.sender_id}
        participants = sorted(author_names)
        now = utc_now_naive()
        state = await self._state_row(session, tenant)
        if result.emotion is not None:
            await self._apply_emotion(session, tenant, result.emotion, now)
        markers = list(
            (await session.execute(select(ChatForgetMarker).where(ChatForgetMarker.tenant == tenant))).scalars()
        )
        subject_markers = {row.subject_id for row in markers if row.scope == "subject"}
        fact_markers = {(row.subject_id, row.fact_key, row.fact_value_hash) for row in markers if row.scope == "fact"}

        episode_ids: list[int] = []
        for index, topic in enumerate(result.topics[:3]):
            episode_key = f"{batch_id}:{index}"
            rows = await ChatEpisode.select_rows(tenant=tenant, episode_key=episode_key, session=session)
            if rows:
                episode = rows[0]
                episode.title = topic.title
                episode.keywords = json.dumps(topic.keywords, ensure_ascii=False)
                episode.summary = topic.summary
                episode.participants = json.dumps(participants, ensure_ascii=False)
                episode.seq_start = seq_start
                episode.seq_end = seq_end
                episode.status = "active"
                episode.source_revision = state.facts_version
                episode.updated_at = now
            else:
                episode = ChatEpisode(
                    tenant=tenant,
                    episode_key=episode_key,
                    batch_id=batch_id,
                    topic_index=index,
                    seq_start=seq_start,
                    seq_end=seq_end,
                    title=topic.title,
                    keywords=json.dumps(topic.keywords, ensure_ascii=False),
                    summary=topic.summary,
                    participants=json.dumps(participants, ensure_ascii=False),
                    emotion_valence=result.emotion[0] if result.emotion else None,
                    emotion_arousal=result.emotion[1] if result.emotion else None,
                    status="active",
                    source_revision=state.facts_version,
                )
                session.add(episode)
                await session.flush()
            episode_ids.append(int(episode.id))

        touched_subjects: set[str] = set()
        written = 0
        for fact in result.facts:
            if fact.subject_id in subject_markers or (fact.subject_id, fact.key, fact.value_hash) in fact_markers:
                continue
            evidence_messages = [message_by_id[seq] for seq in fact.evidence if seq in message_by_id]
            if not evidence_messages:
                continue
            rows = await ChatMemory.select_rows(
                tenant=tenant,
                subject_id=fact.subject_id,
                key=fact.key,
                value_hash=fact.value_hash,
                session=session,
            )
            if rows:
                memory = rows[0]
                memory.subject_name = fact.subject_name or author_names.get(fact.subject_id, memory.subject_name)
                memory.asserted_by = fact.asserted_by
                memory.confidence = min(0.95, max(memory.confidence, fact.confidence))
                memory.importance = max(memory.importance, fact.importance)
                memory.source_batch_id = batch_id
                memory.status = "active"
                memory.updated_at = now
            else:
                memory = ChatMemory(
                    tenant=tenant,
                    subject_id=fact.subject_id,
                    subject_name=fact.subject_name or author_names.get(fact.subject_id, ""),
                    asserted_by=fact.asserted_by,
                    key=fact.key,
                    value=fact.value,
                    value_hash=fact.value_hash,
                    category=fact.category,
                    confidence=fact.confidence,
                    importance=fact.importance,
                    source=source,
                    source_episode_id=episode_ids[0] if episode_ids else None,
                    source_batch_id=batch_id,
                    asserted_at=max(message.occurred_at for message in evidence_messages),
                )
                session.add(memory)
                await session.flush()
            for message in evidence_messages:
                await session.execute(
                    sqlite_insert(ChatMemoryEvidence)
                    .values(
                        tenant=tenant,
                        memory_id=int(memory.id),
                        message_seq=int(message.id),
                        platform_message_id=message.platform_message_id or "",
                        snippet=message.content[:120],
                        status="valid",
                    )
                    .on_conflict_do_nothing(index_elements=["tenant", "memory_id", "message_seq"])
                )
            evidences = list(
                (
                    await session.execute(
                        select(ChatMemoryEvidence).where(
                            ChatMemoryEvidence.tenant == tenant, ChatMemoryEvidence.memory_id == int(memory.id)
                        )
                    )
                ).scalars()
            )
            memory.evidence_count = len(evidences)
            memory.valid_evidence_count = sum(1 for row in evidences if row.status == "valid")
            memory.version += 1
            touched_subjects.add(fact.subject_id)
            written += 1

        relations_written = 0
        if source != "backfill":
            for relation in result.relations:
                if relation.subject_id in subject_markers:
                    continue
                if relation.object_kind == "user" and relation.object_key in subject_markers:
                    continue
                statement = (
                    sqlite_insert(ChatRelation)
                    .values(
                        tenant=tenant,
                        subject_id=relation.subject_id,
                        predicate=relation.predicate,
                        object_kind=relation.object_kind,
                        object_key=relation.object_key,
                        object_name=relation.object_name,
                        object_user_id=relation.object_user_id,
                        confidence=relation.confidence,
                        status="active",
                        created_at=now,
                        updated_at=now,
                    )
                    .on_conflict_do_update(
                        index_elements=["tenant", "subject_id", "predicate", "object_kind", "object_key"],
                        set_={
                            "object_name": relation.object_name,
                            "object_user_id": relation.object_user_id,
                            "confidence": func.max(ChatRelation.confidence, relation.confidence),
                            "status": "active",
                            "updated_at": now,
                        },
                    )
                )
                await session.execute(statement)
                relations_written += 1

        state.facts_version += written
        profile_changes = 0
        for subject_id in touched_subjects:
            if await self._rebuild_profile(session, tenant, subject_id):
                profile_changes += 1

        state.episodes_version += len(episode_ids)
        state.profile_version += profile_changes
        state.updated_at = now
        await session.execute(
            sqlite_insert(ChatJobBatch)
            .values(
                tenant=tenant,
                job=job,
                range_start=seq_start,
                range_end=seq_end,
                algorithm_version=algorithm_version,
                status="done",
                attempt=1,
                completed_at=now,
            )
            .on_conflict_do_update(
                index_elements=["tenant", "job", "range_start", "range_end", "algorithm_version"],
                set_={"status": "done", "completed_at": now, "error": ""},
            )
        )
        await self._upsert_cursor(
            session,
            tenant,
            job,
            high_water if next_cursor is None else int(next_cursor),
            direction=direction,
            last_error="",
        )
        return {"facts": written, "episodes": len(episode_ids), "relations": relations_written}

    @with_session
    async def forget_subject(self, session: AsyncSession, tenant: str, subject_id: str, *, reason: str = "user") -> int:
        """物理删除主体的事实、证据、画像与别名，并写入禁止重建标记。"""
        memories = list(
            (
                await session.execute(
                    select(ChatMemory).where(ChatMemory.tenant == tenant, ChatMemory.subject_id == subject_id)
                )
            ).scalars()
        )
        memory_ids = [int(row.id) for row in memories if row.id is not None]
        if memory_ids:
            await session.execute(
                delete(ChatMemoryEvidence).where(
                    ChatMemoryEvidence.tenant == tenant, ChatMemoryEvidence.memory_id.in_(memory_ids)
                )
            )
        await session.execute(
            delete(ChatMemory).where(ChatMemory.tenant == tenant, ChatMemory.subject_id == subject_id)
        )
        await session.execute(
            delete(ChatProfile).where(ChatProfile.tenant == tenant, ChatProfile.subject_id == subject_id)
        )
        await session.execute(delete(ChatAlias).where(ChatAlias.tenant == tenant, ChatAlias.user_id == subject_id))
        await session.execute(
            sqlite_insert(ChatForgetMarker)
            .values(tenant=tenant, scope="subject", subject_id=subject_id, reason=reason)
            .on_conflict_do_nothing(index_elements=["tenant", "scope", "subject_id", "fact_key", "fact_value_hash"])
        )
        # 隐私策略：本人原始消息置空，不保留可反查内容
        await session.execute(
            update(ChatMessage)
            .where(ChatMessage.tenant == tenant, ChatMessage.sender_id == subject_id)
            .values(content="[已忘记]", payload="{}")
        )
        # 主体既可能出现在边的一端，也可能作为 user 目标出现在另一端
        await session.execute(
            delete(ChatRelation).where(ChatRelation.tenant == tenant, ChatRelation.subject_id == subject_id)
        )
        await session.execute(
            delete(ChatRelation).where(
                ChatRelation.tenant == tenant,
                ChatRelation.object_kind == "user",
                ChatRelation.object_key == subject_id,
            )
        )
        stale_episodes = await self._mark_episodes_stale(session, tenant, {subject_id})
        state = await self._state_row(session, tenant)
        state.facts_version += len(memory_ids)
        state.episodes_version += stale_episodes
        state.profile_version += 1
        state.updated_at = utc_now_naive()
        return len(memory_ids)

    @with_session
    async def forget_fact(
        self, session: AsyncSession, tenant: str, subject_id: str, keyword: str, *, reason: str = "user"
    ) -> list[tuple[str, str]]:
        """按关键词删除主体的事实，并逐条写 fact 级标记。"""
        needle = keyword.strip().casefold()
        memories = list(
            (
                await session.execute(
                    select(ChatMemory).where(ChatMemory.tenant == tenant, ChatMemory.subject_id == subject_id)
                )
            ).scalars()
        )
        matched = [
            row for row in memories if not needle or needle in row.key.casefold() or needle in row.value.casefold()
        ]
        if not matched:
            return []
        memory_ids = [int(row.id) for row in matched if row.id is not None]
        await session.execute(
            delete(ChatMemoryEvidence).where(
                ChatMemoryEvidence.tenant == tenant, ChatMemoryEvidence.memory_id.in_(memory_ids)
            )
        )
        for row in matched:
            await session.execute(
                sqlite_insert(ChatForgetMarker)
                .values(
                    tenant=tenant,
                    scope="fact",
                    subject_id=subject_id,
                    fact_key=row.key,
                    fact_value_hash=row.value_hash,
                    reason=reason,
                )
                .on_conflict_do_nothing(index_elements=["tenant", "scope", "subject_id", "fact_key", "fact_value_hash"])
            )
        await session.execute(delete(ChatMemory).where(ChatMemory.tenant == tenant, ChatMemory.id.in_(memory_ids)))
        await self._rebuild_profile(session, tenant, subject_id)
        state = await self._state_row(session, tenant)
        state.facts_version += len(memory_ids)
        state.profile_version += 1
        state.updated_at = utc_now_naive()
        return [(row.key, row.value) for row in matched]

    @with_session
    async def invalidate_message(
        self, session: AsyncSession, tenant: str, platform_message_id: str, *, operator: str = ""
    ) -> dict[str, int] | None:
        """撤回消息：作废证据、失效事实与 Episode、标记画像 stale。"""
        rows = await ChatMessage.select_rows(tenant=tenant, platform_message_id=platform_message_id, session=session)
        if not rows:
            return None
        message = rows[0]
        now = utc_now_naive()
        await session.execute(
            update(ChatMessage)
            .where(ChatMessage.tenant == tenant, ChatMessage.id == message.id)
            .values(deleted=True, deleted_at=now, delete_operator=operator)
        )
        evidences = list(
            (
                await session.execute(
                    select(ChatMemoryEvidence).where(
                        ChatMemoryEvidence.tenant == tenant, ChatMemoryEvidence.message_seq == int(message.id)
                    )
                )
            ).scalars()
        )
        memory_ids = {row.memory_id for row in evidences}
        for evidence in evidences:
            evidence.status = "invalid"
        forgotten = 0
        subjects: set[str] = set()
        for memory_id in memory_ids:
            memory_rows = await ChatMemory.select_rows(tenant=tenant, id=memory_id, session=session)
            if not memory_rows:
                continue
            memory = memory_rows[0]
            subject_rows = list(
                (
                    await session.execute(
                        select(ChatMemoryEvidence).where(
                            ChatMemoryEvidence.tenant == tenant, ChatMemoryEvidence.memory_id == memory_id
                        )
                    )
                ).scalars()
            )
            valid = sum(1 for row in subject_rows if row.status == "valid")
            memory.evidence_count = len(subject_rows)
            memory.valid_evidence_count = valid
            subjects.add(memory.subject_id)
            if valid == 0 and memory.status == "active":
                memory.status = "forgotten"
                memory.version += 1
                forgotten += 1
        episodes = list(
            (
                await session.execute(
                    select(ChatEpisode).where(
                        ChatEpisode.tenant == tenant,
                        ChatEpisode.status == "active",
                        ChatEpisode.seq_start <= int(message.id),
                        ChatEpisode.seq_end >= int(message.id),
                    )
                )
            ).scalars()
        )
        for episode in episodes:
            episode.status = "stale"
            episode.updated_at = now
        for subject_id in subjects - {message.sender_id}:
            await self._mark_profile_stale(session, tenant, subject_id, "撤回证据失效")
        styles = list((await session.execute(select(ChatStyle).where(ChatStyle.tenant == tenant))).scalars())
        for style in styles:
            if not int(style.evidence_seq_start) <= int(message.id) <= int(style.evidence_seq_end):
                continue
            try:
                examples = json.loads(style.examples or "[]")
            except (TypeError, ValueError):
                examples = []
            if isinstance(examples, list):
                kept = [
                    item
                    for item in examples
                    if not (isinstance(item, dict) and str(item.get("seq") or "") == str(message.id))
                ]
                if len(kept) != len(examples):
                    style.examples = json.dumps(kept, ensure_ascii=False)
                    style.updated_at = now
        stale_batches: list[str] = []
        jargon_rows = list((await session.execute(select(ChatJargon).where(ChatJargon.tenant == tenant))).scalars())
        for row in jargon_rows:
            parts = str(row.source_batch_id or "").split(":")
            if len(parts) != 3 or parts[0] != "style":
                continue
            try:
                start, end = int(parts[1]), int(parts[2])
            except ValueError:
                continue
            if start <= int(message.id) <= end:
                stale_batches.append(row.source_batch_id)
        if stale_batches:
            await session.execute(
                delete(ChatJargon).where(
                    ChatJargon.tenant == tenant,
                    ChatJargon.source_batch_id.in_(stale_batches),
                )
            )
        stale_expression_ids: list[int] = []
        expression_rows = list(
            (await session.execute(select(ChatExpression).where(ChatExpression.tenant == tenant))).scalars()
        )
        for row in expression_rows:
            if int(row.source_message_seq) == int(message.id):
                stale_expression_ids.append(int(row.id or 0))
                continue
            parts = str(row.source_batch_id or "").split(":")
            if len(parts) != 3 or parts[0] != "expression":
                continue
            try:
                start, end = int(parts[1]), int(parts[2])
            except ValueError:
                continue
            if start <= int(message.id) <= end:
                stale_expression_ids.append(int(row.id or 0))
        if stale_expression_ids:
            await session.execute(
                delete(ChatExpression).where(
                    ChatExpression.tenant == tenant,
                    ChatExpression.id.in_(stale_expression_ids),
                )
            )
        state = await self._state_row(session, tenant)
        state.facts_version += forgotten
        state.episodes_version += len(episodes)
        state.profile_version += len(subjects)
        state.updated_at = now
        return {"memories": len(memory_ids), "forgotten": forgotten, "episodes": len(episodes)}

    async def _write_alias(
        self, session: AsyncSession, tenant: str, user_id: str, alias: str, source: str = "nickname"
    ) -> None:
        """写入一条昵称别名；空值直接跳过。"""
        alias = " ".join(str(alias or "").split())[:64]
        if not tenant or not user_id or not alias:
            return
        timestamp = utc_now_naive()
        statement = (
            sqlite_insert(ChatAlias)
            .values(
                tenant=tenant,
                user_id=str(user_id),
                alias=alias,
                normalized=alias.casefold(),
                source=source,
                first_seen=timestamp,
                last_seen=timestamp,
            )
            .on_conflict_do_update(
                index_elements=["tenant", "user_id", "alias"],
                set_={"last_seen": timestamp, "source": source},
            )
        )
        await session.execute(statement)

    async def _chat_state_row(
        self, session: AsyncSession, tenant: str, subject_id: str, *, create: bool = True
    ) -> ChatState | None:
        """读取或新建情绪行；调用方事务内使用。"""
        rows = await ChatState.select_rows(tenant=tenant, subject_id=subject_id, session=session)
        if rows:
            return rows[0]
        if not create:
            return None
        row = ChatState(tenant=tenant, subject_id=subject_id)
        session.add(row)
        await session.flush()
        return row

    @staticmethod
    def _decay_state(row: ChatState, now: datetime, decay_per_day: float) -> None:
        """按上海日期把情绪拉向 0，并按天衰减熟悉度；每天只应用一次。"""
        if row.last_decay_at is None:
            row.last_decay_at = now
            return
        days = (as_shanghai(now).date() - as_shanghai(row.last_decay_at).date()).days
        if days <= 0:
            return
        row.valence = _toward_zero(row.valence, decay_per_day * days)
        row.arousal = _toward_zero(row.arousal, decay_per_day * days)
        row.familiarity = min(1.0, max(0.0, row.familiarity * (0.99**days)))
        row.last_decay_at = now

    async def _apply_emotion(
        self, session: AsyncSession, tenant: str, emotion: tuple[float, float], now: datetime
    ) -> None:
        if not mood_enabled():
            return
        valence = _clamp(float(emotion[0]), -1.0, 1.0)
        arousal = _clamp(float(emotion[1]), 0.0, 1.0)
        row = await self._chat_state_row(session, tenant, "", create=False)
        if row is None:
            session.add(
                ChatState(
                    tenant=tenant,
                    subject_id="",
                    valence=valence,
                    arousal=arousal,
                    last_decay_at=now,
                    updated_at=now,
                )
            )
            await session.flush()
            return
        self._decay_state(row, now, _mood_decay_per_day())
        row.valence = 0.7 * row.valence + 0.3 * valence
        row.arousal = 0.7 * row.arousal + 0.3 * arousal
        row.updated_at = now

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

    async def _rebuild_profile(self, session: AsyncSession, tenant: str, subject_id: str) -> bool:
        """用当前有效事实重建画像摘要；stale 期间也会重建。"""
        facts = list(
            (
                await session.execute(
                    select(ChatMemory)
                    .where(
                        ChatMemory.tenant == tenant,
                        ChatMemory.subject_id == subject_id,
                        ChatMemory.status == "active",
                        ChatMemory.valid_evidence_count > 0,
                    )
                    .order_by(ChatMemory.importance.desc(), ChatMemory.confidence.desc())
                    .limit(20)
                )
            ).scalars()
        )
        rows = await ChatProfile.select_rows(tenant=tenant, subject_id=subject_id, session=session)
        if not facts:
            if rows:
                await session.execute(
                    delete(ChatProfile).where(ChatProfile.tenant == tenant, ChatProfile.subject_id == subject_id)
                )
                return True
            return False
        aliases = list(
            (
                await session.execute(
                    select(ChatAlias)
                    .where(ChatAlias.tenant == tenant, ChatAlias.user_id == subject_id)
                    .order_by(ChatAlias.last_seen.desc())
                    .limit(3)
                )
            ).scalars()
        )
        display_name = (aliases[0].alias if aliases else facts[0].subject_name) or facts[0].subject_name
        sections: dict[str, list[str]] = {}
        for fact in facts:
            label = fact.key.split(".", 1)[-1]
            sections.setdefault(fact.category, []).append(f"{label}：{fact.value}")
        summary = "；".join(f"{fact.key.split('.', 1)[-1]}：{fact.value}" for fact in facts)[:300]
        now = utc_now_naive()
        if rows:
            profile = rows[0]
            changed = profile.summary != summary or profile.display_name != display_name
            profile.display_name = display_name
            profile.sections = json.dumps(sections, ensure_ascii=False)
            profile.summary = summary
            profile.evidence_ids = json.dumps([int(fact.id) for fact in facts if fact.id is not None])
            profile.source_versions = json.dumps(
                {"facts": await self._facts_version(session, tenant)}, ensure_ascii=False
            )
            profile.version += 1 if changed else 0
            profile.stale = False
            profile.stale_reason = ""
            profile.last_built_at = now
            profile.updated_at = now
            return changed
        profile = ChatProfile(
            tenant=tenant,
            subject_id=subject_id,
            display_name=display_name,
            sections=json.dumps(sections, ensure_ascii=False),
            summary=summary,
            evidence_ids=json.dumps([int(fact.id) for fact in facts if fact.id is not None]),
            source_versions=json.dumps({"facts": await self._facts_version(session, tenant)}, ensure_ascii=False),
            version=1,
            last_built_at=now,
        )
        session.add(profile)
        return True

    async def _facts_version(self, session: AsyncSession, tenant: str) -> int:
        """读取当前事实版本，供画像快照使用。"""
        rows = await ChatSessionState.select_rows(tenant=tenant, session=session)
        return int(rows[0].facts_version) if rows else 0

    async def _mark_profile_stale(self, session: AsyncSession, tenant: str, subject_id: str, reason: str) -> None:
        """把画像标为 stale，停止注入直到重建。"""
        rows = await ChatProfile.select_rows(tenant=tenant, subject_id=subject_id, session=session)
        if not rows:
            return
        rows[0].stale = True
        rows[0].stale_reason = reason
        rows[0].updated_at = utc_now_naive()

    async def _mark_episodes_stale(self, session: AsyncSession, tenant: str, subject_ids: set[str]) -> int:
        """把包含指定主体的 Episode 标为 stale。"""
        episodes = list((await session.execute(select(ChatEpisode).where(ChatEpisode.tenant == tenant))).scalars())
        changed = 0
        for episode in episodes:
            try:
                participants = set(json.loads(episode.participants or "[]"))
            except (TypeError, ValueError):
                participants = set()
            if participants & subject_ids and episode.status == "active":
                episode.status = "stale"
                changed += 1
        return changed


chat_repository = ChatRepository()
