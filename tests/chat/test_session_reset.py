"""重置会话：清空短期历史与长期记忆，保留学习记录与开关。"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from uuid import uuid4
from unittest.mock import AsyncMock, MagicMock, patch

from src.plugins.nonebot_plugin_fxbot.db import base_models
from src.plugins.nonebot_plugin_fxbot.chat.memory import state as memory_state, migrate
from src.plugins.nonebot_plugin_fxbot.chat.service import ChatService
from src.plugins.nonebot_plugin_fxbot.chat.session import ChatSessionStore
from src.plugins.nonebot_plugin_fxbot.chat.memory.models import (
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
)
from src.plugins.nonebot_plugin_fxbot.chat.amemorix.models import ChatPerson
from src.plugins.nonebot_plugin_fxbot.chat.amemorix.service import (
    _delete_tenant_records,
    _tenant_paragraph_hashes,
)
from src.plugins.nonebot_plugin_fxbot.chat.memory.repository import chat_repository
from src.plugins.nonebot_plugin_fxbot.chat.amemorix.core.storage.metadata_store import MetadataStore

from .support import ReplyPipelineTests

_TENANT_MODELS = (
    ChatTurnItem,
    ChatTurn,
    ChatMessage,
    ChatPlannerSample,
    ChatRecallTombstone,
    ChatJobCursor,
    ChatStyle,
    ChatJargon,
    ChatExpression,
    ChatEmoji,
    ChatTrace,
    ChatPerson,
)


def _rows(tenant: str) -> list:
    return [
        ChatMessage(tenant=tenant, content="hi", dedupe_key=f"msg:{uuid4().hex}"),
        ChatTurn(tenant=tenant, turn_key=uuid4().hex),
        ChatTurnItem(tenant=tenant, role="user", content="hi"),
        ChatPlannerSample(tenant=tenant, turn_id=uuid4().hex),
        ChatRecallTombstone(tenant=tenant, platform_message_id=uuid4().hex),
        ChatJobCursor(tenant=tenant, job="memory"),
        ChatStyle(tenant=tenant),
        ChatJargon(tenant=tenant, term="梗"),
        ChatExpression(
            tenant=tenant,
            situation="在干嘛",
            situation_hash=uuid4().hex,
            style="哈哈",
            style_hash=uuid4().hex,
        ),
        ChatEmoji(tenant=tenant, hash=uuid4().hex, path="emoji.png"),
        ChatTrace(tenant=tenant),
        ChatPerson(tenant=tenant, person_id=uuid4().hex, user_id=str(uuid4().int)),
    ]


class SessionResetTests(ReplyPipelineTests):
    """重置删除短期历史与长期记忆，学习记录在软硬清空下都保留。"""

    async def asyncSetUp(self) -> None:
        await base_models.init_database()
        await migrate.ensure_schema()
        async with base_models.engine.begin() as conn:
            await conn.run_sync(ChatPerson.__table__.create, checkfirst=True)
        memory_state.mark_ready()
        self.addCleanup(memory_state.mark_ready)
        self.tenant = f"reset-{uuid4().hex}"
        self.other = f"reset-{uuid4().hex}"
        self.addAsyncCleanup(chat_repository.clear_tenant, self.tenant)
        self.addAsyncCleanup(chat_repository.clear_tenant, self.other)
        await self._seed()

    async def _seed(self) -> None:
        async with base_models.async_maker() as session:
            for tenant in (self.tenant, self.other):
                for row in _rows(tenant):
                    session.add(row)
                session.add(
                    ChatSessionState(
                        tenant=tenant,
                        generation=3,
                        learn_enabled=True,
                        proactive_enabled=False,
                    )
                )
            await session.commit()

    async def _remaining(self, tenant: str) -> dict[str, int]:
        async with base_models.async_maker() as session:
            return {
                model.__tablename__: len(await model.select_rows(tenant=tenant, session=session))
                for model in _TENANT_MODELS
            }

    async def test_reset_clears_history_and_keeps_learning(self) -> None:
        state = await chat_repository.get_session_state(self.tenant)
        person_ids = [row.person_id for row in await ChatPerson.select_rows(tenant=self.tenant)]

        reset = await chat_repository.reset_tenant(self.tenant)

        remaining = await self._remaining(self.tenant)
        for model in (ChatMessage, ChatTurn, ChatTurnItem, ChatRecallTombstone, ChatTrace):
            self.assertEqual(remaining[model.__tablename__], 0)
        for model in (ChatPlannerSample, ChatJobCursor, ChatStyle, ChatJargon, ChatExpression, ChatEmoji, ChatPerson):
            self.assertEqual(remaining[model.__tablename__], 1)
        self.assertEqual(set((await self._remaining(self.other)).values()), {1})
        self.assertEqual(reset.generation, state.generation + 1)
        self.assertEqual(sorted(reset.person_ids), sorted(person_ids))
        kept = await chat_repository.get_session_state(self.tenant)
        self.assertEqual(kept.generation, state.generation + 1)
        self.assertTrue(kept.learn_enabled)
        self.assertFalse(kept.proactive_enabled)

    async def test_service_reset_clears_long_term_memory(self) -> None:
        service = ChatService(ChatSessionStore())
        person_ids = sorted(row.person_id for row in await ChatPerson.select_rows(tenant=self.tenant))
        wipe = AsyncMock(return_value={"success": True})
        forget = MagicMock()
        with (
            patch("src.plugins.nonebot_plugin_fxbot.chat.amemorix.service.wipe_tenant", wipe),
            patch("src.plugins.nonebot_plugin_fxbot.chat.amemorix.writeback.chat_writeback.forget_session", forget),
            patch(
                "src.plugins.nonebot_plugin_fxbot.chat.amemorix.image_writeback.image_writeback.forget_session", forget
            ),
        ):
            cleared = await service.reset_session(self.tenant)

        self.assertTrue(cleared)
        wipe.assert_awaited_once()
        self.assertEqual(wipe.await_args.args[0], self.tenant)
        self.assertEqual(sorted(wipe.await_args.args[1]), person_ids)
        self.assertEqual(forget.call_count, 2)
        self.assertEqual(service.session_store.epoch(self.tenant), 4)

    async def test_service_reset_reports_memory_failure(self) -> None:
        service = ChatService(ChatSessionStore())
        with (
            patch(
                "src.plugins.nonebot_plugin_fxbot.chat.amemorix.service.wipe_tenant",
                AsyncMock(return_value={"success": False, "error": "boom"}),
            ),
            patch("src.plugins.nonebot_plugin_fxbot.chat.amemorix.writeback.chat_writeback.forget_session"),
            patch("src.plugins.nonebot_plugin_fxbot.chat.amemorix.image_writeback.image_writeback.forget_session"),
        ):
            cleared = await service.reset_session(self.tenant)

        self.assertFalse(cleared)
        remaining = await self._remaining(self.tenant)
        for model in (ChatMessage, ChatTurn, ChatTurnItem):
            self.assertEqual(remaining[model.__tablename__], 0)
        for model in (ChatStyle, ChatEmoji, ChatPerson):
            self.assertEqual(remaining[model.__tablename__], 1)

    async def test_soft_clear_keeps_learned_rows(self) -> None:
        service = ChatService(ChatSessionStore())
        with patch("src.plugins.nonebot_plugin_fxbot.chat.amemorix.service.wipe_tenant") as wipe:
            await service.clear_session(self.tenant)

        wipe.assert_not_called()
        remaining = await self._remaining(self.tenant)
        for model in (ChatStyle, ChatJargon, ChatExpression, ChatEmoji, ChatPerson, ChatJobCursor):
            self.assertEqual(remaining[model.__tablename__], 1)
        for model in (ChatMessage, ChatTurn, ChatTurnItem, ChatPlannerSample):
            self.assertEqual(remaining[model.__tablename__], 0)

    async def test_reset_tenant_is_safe_without_persons(self) -> None:
        async with base_models.async_maker() as session:
            for row in await ChatPerson.select_rows(tenant=self.tenant, session=session):
                await session.delete(row)
            await session.commit()

        reset = await chat_repository.reset_tenant(self.tenant)

        self.assertEqual(reset.person_ids, ())
        remaining = await self._remaining(self.tenant)
        self.assertEqual(remaining[ChatMessage.__tablename__], 0)
        self.assertEqual(remaining[ChatStyle.__tablename__], 1)


class WipeTenantSqlTests(unittest.TestCase):
    """长期记忆清理 SQL：只删除本会话与本人物的记录。"""

    def setUp(self) -> None:
        folder = tempfile.mkdtemp(prefix="fxbot-wipe-")
        self.addCleanup(shutil.rmtree, folder, True)
        self.store = MetadataStore(folder)
        self.store.connect()
        self.addCleanup(self.store.close)
        self.tenant, self.other = f"wipe-{uuid4().hex}", f"wipe-{uuid4().hex}"
        self.person, self.other_person = f"p-{uuid4().hex}", f"p-{uuid4().hex}"
        self._seed()

    def _seed(self) -> None:
        with self.store.transaction(immediate=True) as conn:
            for index, (tenant, source, metadata) in enumerate(
                (
                    (self.tenant, "chat", '{"chat_id": "%s"}' % self.tenant),
                    (self.tenant, f"chat_summary:{self.tenant}", "{}"),
                    (self.other, "chat", '{"chat_id": "%s"}' % self.other),
                    (self.other, "note", "{}"),
                )
            ):
                conn.execute(
                    "INSERT INTO paragraphs(hash, content, source, metadata, is_deleted) VALUES (?,?,?,?,0)",
                    (f"h{index}", "text", source, metadata),
                )
            claims = (
                ("c-chat", "chat", self.tenant),
                ("c-person", "person", self.person),
                ("c-other", "chat", self.other),
            )
            for claim_id, scope, scope_id in claims:
                conn.execute(
                    "INSERT INTO fact_claims(claim_id, scope_type, scope_id, fact_key, value_text,"
                    " value_normalized, polarity, cardinality, conflict_group, stability, profile_section,"
                    " authority, status, first_observed_at, last_confirmed_at, created_at, updated_at)"
                    " VALUES (?,?,?,'f','v','v','positive','single','g','stable','stable_facts','direct_user',"
                    " 'active',0,0,0,0)",
                    (claim_id, scope, scope_id),
                )
                conn.execute(
                    "INSERT INTO fact_evidence(claim_id, evidence_type, evidence_id, stance, observed_at)"
                    " VALUES (?,'message',?, 'support', 0)",
                    (claim_id, claim_id),
                )
            for person_id in (self.person, self.other_person):
                conn.execute(
                    "INSERT INTO person_profile_snapshots(person_id, profile_version, profile_text, updated_at)"
                    " VALUES (?,1,'t',0)",
                    (person_id,),
                )
                conn.execute(
                    "INSERT INTO person_profile_overrides(person_id, override_text, updated_at) VALUES (?,'t',0)",
                    (person_id,),
                )
                conn.execute(
                    "INSERT INTO person_profile_alias_overrides(person_id, aliases_json, updated_at) VALUES (?,'[]',0)",
                    (person_id,),
                )
                conn.execute(
                    "INSERT INTO person_profile_refresh_queue(person_id, requested_at, updated_at) VALUES (?,0,0)",
                    (person_id,),
                )
                conn.execute(
                    "INSERT INTO person_profile_active_persons(stream_id, user_id, person_id, last_seen_at)"
                    " VALUES ('s','u',?,0)",
                    (person_id,),
                )
            for plan_id, chat_id, person_id in (
                ("p-chat", self.tenant, None),
                ("p-person", None, self.person),
                ("p-other", self.other, self.other_person),
            ):
                conn.execute(
                    "INSERT INTO memory_fuzzy_modify_plans(plan_id, request_text, scope, target_person_id,"
                    " target_chat_id, status, plan_json, created_at, updated_at) VALUES (?,'r','chat',?,?,"
                    " 'pending','{}',0,0)",
                    (plan_id, person_id, chat_id),
                )
            for index, chat_id in enumerate((self.tenant, self.other)):
                conn.execute(
                    "INSERT INTO chat_summary_checkpoints(external_id, chat_id, created_at) VALUES (?,?,0)",
                    (f"e{index}", chat_id),
                )
            for index, session_id in enumerate((self.tenant, self.other)):
                cursor = conn.execute(
                    "INSERT INTO memory_feedback_tasks(query_tool_id, session_id, query_timestamp, due_at,"
                    " created_at, updated_at) VALUES (?,?,0,0,0,0)",
                    (f"q{index}", session_id),
                )
                conn.execute(
                    "INSERT INTO memory_feedback_action_logs(task_id, query_tool_id, action_type, created_at)"
                    " VALUES (?,?,'delete',0)",
                    (cursor.lastrowid, f"q{index}"),
                )

    def _count(self, table: str, where: str, params: tuple) -> int:
        return int(self.store.query(f"SELECT COUNT(*) AS n FROM {table} WHERE {where}", params)[0]["n"])

    def test_tenant_paragraph_hashes_only_match_own_chat(self) -> None:
        self.assertEqual(sorted(_tenant_paragraph_hashes(self.store, self.tenant)), ["h0", "h1"])

    def test_delete_tenant_records_keeps_other_tenant_and_person(self) -> None:
        _delete_tenant_records(self.store, self.tenant, [self.person])

        self.assertEqual(self._count("fact_claims", "scope_type='chat' AND scope_id=?", (self.tenant,)), 0)
        self.assertEqual(self._count("fact_claims", "scope_type='person' AND scope_id=?", (self.person,)), 0)
        self.assertEqual(
            self._count("fact_evidence", "claim_id IN ('c-chat','c-person')", ()),
            0,
        )
        self.assertEqual(self._count("fact_claims", "claim_id='c-other'", ()), 1)
        self.assertEqual(self._count("fact_evidence", "claim_id='c-other'", ()), 1)
        for table in (
            "person_profile_snapshots",
            "person_profile_overrides",
            "person_profile_alias_overrides",
            "person_profile_refresh_queue",
            "person_profile_active_persons",
        ):
            self.assertEqual(self._count(table, "person_id=?", (self.person,)), 0, table)
            self.assertEqual(self._count(table, "person_id=?", (self.other_person,)), 1, table)
        self.assertEqual(self._count("memory_fuzzy_modify_plans", "plan_id='p-chat'", ()), 0)
        self.assertEqual(self._count("memory_fuzzy_modify_plans", "plan_id='p-person'", ()), 0)
        self.assertEqual(self._count("memory_fuzzy_modify_plans", "plan_id='p-other'", ()), 1)
        self.assertEqual(self._count("chat_summary_checkpoints", "chat_id=?", (self.tenant,)), 0)
        self.assertEqual(self._count("chat_summary_checkpoints", "chat_id=?", (self.other,)), 1)
        self.assertEqual(self._count("memory_feedback_tasks", "session_id=?", (self.tenant,)), 0)
        self.assertEqual(self._count("memory_feedback_tasks", "session_id=?", (self.other,)), 1)
        self.assertEqual(self._count("memory_feedback_action_logs", "query_tool_id='q0'", ()), 0)
        self.assertEqual(self._count("memory_feedback_action_logs", "query_tool_id='q1'", ()), 1)

    def test_delete_tenant_records_without_persons_still_clears_chat_plans(self) -> None:
        _delete_tenant_records(self.store, self.tenant, [])

        self.assertEqual(self._count("memory_fuzzy_modify_plans", "plan_id='p-chat'", ()), 0)
        self.assertEqual(self._count("memory_fuzzy_modify_plans", "plan_id='p-other'", ()), 1)
