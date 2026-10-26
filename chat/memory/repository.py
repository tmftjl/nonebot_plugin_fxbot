"""记忆历史的租户级持久化。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from sqlmodel import select
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ...db import with_session
from .models import (
    ChatTurn,
    ChatAlias,
    ChatMemory,
    ChatEpisode,
    ChatMessage,
    ChatProfile,
    ChatJobBatch,
    ChatTurnItem,
    ChatJobCursor,
    ChatForgetMarker,
    ChatSessionState,
    ChatMemoryEvidence,
    utc_now_naive,
)


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
    async def pending_tenants(self, session: AsyncSession, limit: int = 20) -> list[str]:
        """列出还有未处理人类消息的租户。"""
        statement = (
            select(ChatMessage.tenant)
            .where(ChatMessage.kind == "user", ChatMessage.deleted.is_(False))
            .group_by(ChatMessage.tenant)
            .limit(limit)
        )
        return [str(row) for row in (await session.execute(statement)).scalars().all()]

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
    ) -> dict[str, int]:
        """在一个事务里写 Episode、事实、证据、画像并推进游标。"""
        from .extract import ExtractionResult

        if not messages or not isinstance(result, ExtractionResult):
            return {"facts": 0, "episodes": 0}
        message_by_id = {int(message.id): message for message in messages if message.id is not None}
        if not message_by_id:
            return {"facts": 0, "episodes": 0}
        seq_start, seq_end = min(message_by_id), max(high_water, min(message_by_id))
        author_names = {message.sender_id: message.sender_name for message in messages if message.sender_id}
        participants = sorted(author_names)
        now = utc_now_naive()
        state = await self._state_row(session, tenant)
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
                    source="extract",
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
        await self._upsert_cursor(session, tenant, job, high_water, last_error="")
        return {"facts": written, "episodes": len(episode_ids)}

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
        await self._mark_episodes_stale(session, tenant, {subject_id})
        state = await self._state_row(session, tenant)
        state.facts_version += len(memory_ids)
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
        fail_count: int = 0,
        next_retry_at: datetime | None = None,
        last_error: str = "",
    ) -> None:
        """写入游标；成功时清空退避状态。"""
        now = utc_now_naive()
        statement = (
            sqlite_insert(ChatJobCursor)
            .values(
                tenant=tenant,
                job=job,
                direction="forward",
                cursor_seq=int(cursor_seq),
                fail_count=fail_count,
                next_retry_at=next_retry_at,
                last_error=last_error,
                updated_at=now,
            )
            .on_conflict_do_update(
                index_elements=["tenant", "job"],
                set_={
                    "cursor_seq": int(cursor_seq),
                    "fail_count": fail_count,
                    "next_retry_at": next_retry_at,
                    "last_error": last_error,
                    "updated_at": now,
                },
            )
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
