"""历史持久化、恢复与清空测试。"""

from __future__ import annotations

import json
import asyncio
import tempfile
import unittest
from uuid import uuid4
from types import SimpleNamespace
from pathlib import Path
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

from sqlmodel import select
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from src.plugins.nonebot_plugin_fxbot.db import base_models
from src.plugins.nonebot_plugin_fxbot.chat import router, service as service_module
from src.plugins.nonebot_plugin_fxbot.chat.tools import ToolRuntime
from src.plugins.nonebot_plugin_fxbot.chat.types import ChatRequest, ChatResponse
from src.plugins.nonebot_plugin_fxbot.chat.memory import state as memory_state, migrate
from src.plugins.nonebot_plugin_fxbot.chat.service import ChatService
from src.plugins.nonebot_plugin_fxbot.chat.session import ChatSessionStore
from src.plugins.nonebot_plugin_fxbot.chat.providers import LLMResponse, provider_manager
from src.plugins.nonebot_plugin_fxbot.chat.memory.models import ChatTurn, ChatMessage, ChatTurnItem
from src.plugins.nonebot_plugin_fxbot.chat.behavior.debounce import PendingBatch
from src.plugins.nonebot_plugin_fxbot.chat.memory.repository import ChatRepository, chat_repository


def _utc(hour: int) -> datetime:
    """构造测试用 naive UTC。"""
    return datetime(2026, 1, 1, hour, tzinfo=timezone.utc).replace(tzinfo=None)


def _request(**values: object) -> ChatRequest:
    """构造最小对话请求。"""
    fields = {
        "session_id": "tenant",
        "user_id": "user",
        "group_id": "group",
        "text": "你好",
        "turn_id": "turn-1",
        "dedupe_key": "msg:1",
        "trigger_kind": "mention",
    }
    fields.update(values)
    return ChatRequest(**fields)  # type: ignore[arg-type]


class _FakeConfig:
    """只提供对话测试需要的配置。"""

    def get_system(self) -> dict[str, object]:
        return {
            "chat": {
                "max_history": 10,
                "image_description": False,
                "persona": "",
                "provider": "",
                "enabled_tools": ["__disabled__"],
            }
        }


class _FakeProvider:
    """固定返回一条回复。"""

    async def chat(self, messages: list[dict[str, object]], tools: object = None) -> LLMResponse:
        return LLMResponse(content="回复")


@asynccontextmanager
async def _fake_lease(_name: str):
    yield _FakeProvider()


class RepositoryTests(unittest.IsolatedAsyncioTestCase):
    """临时 SQLite 上的租户级读写测试。"""

    async def asyncSetUp(self) -> None:
        await base_models.init_database()
        self._tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tempdir.cleanup)
        path = Path(self._tempdir.name) / "history.db"
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
        self.addAsyncCleanup(self.engine.dispose)
        self.maker = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)
        await migrate.ensure_schema(self.engine)
        self.repo = ChatRepository()

    async def _message(self, session: AsyncSession, tenant: str, key: str, content: str, hour: int) -> int:
        return await self.repo.append_message(
            ChatMessage(tenant=tenant, dedupe_key=key, content=content, occurred_at=_utc(hour)), session=session
        )

    async def _assistant(
        self, session: AsyncSession, tenant: str, content: str, hour: int, *, status: str = "sent", visible: bool = True
    ) -> None:
        turn_key = f"{tenant}-{content}"
        turn = ChatTurn(tenant=tenant, turn_key=turn_key, status=status)
        turn_id = await self.repo.upsert_turn(turn, session=session)
        await self.repo.append_turn_item(
            ChatTurnItem(
                tenant=tenant,
                turn_id=turn_id,
                item_seq=1,
                role="assistant",
                content=content,
                visible=visible,
                send_status=status,
                created_at=_utc(hour),
            ),
            session=session,
        )

    async def test_append_message_is_idempotent(self) -> None:
        async with self.maker() as session:
            first = await self._message(session, "tenant", "msg:1", "first", 1)
            second = await self.repo.append_message(
                ChatMessage(tenant="tenant", dedupe_key="msg:1", content="second"), session=session
            )
            await session.commit()

        self.assertEqual(first, second)
        async with self.maker() as session:
            rows = (await session.execute(select(ChatMessage))).scalars().all()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].content, "first")

    async def test_update_content_skips_deleted_rows(self) -> None:
        async with self.maker() as session:
            live = await self._message(session, "tenant", "msg:1", "old", 1)
            deleted = await self._message(session, "tenant", "msg:2", "old", 2)
            await session.execute(update(ChatMessage).where(ChatMessage.id == deleted).values(deleted=True))
            await self.repo.update_message_content("tenant", live, "new", session=session)
            await self.repo.update_message_content("tenant", deleted, "new", session=session)
            await session.commit()

        async with self.maker() as session:
            rows = {row.id: row.content for row in (await session.execute(select(ChatMessage))).scalars().all()}
        self.assertEqual(rows[live], "new")
        self.assertEqual(rows[deleted], "old")

    async def test_recent_messages_are_tenant_scoped_and_ordered(self) -> None:
        async with self.maker() as session:
            await self._message(session, "a", "msg:1", "一", 1)
            await self._message(session, "a", "msg:2", "二", 2)
            await self._assistant(session, "a", "回复", 3)
            await self._assistant(session, "a", "失败轮", 4, status="failed")
            await self._assistant(session, "a", "未发送", 5, visible=False)
            await self._message(session, "b", "msg:1", "别的租户", 1)
            await self._assistant(session, "b", "别的回复", 2)
            await session.commit()

        async with self.maker() as session:
            a_history = await self.repo.get_recent_messages("a", 10, session=session)
            b_history = await self.repo.get_recent_messages("b", 10, session=session)
            limited = await self.repo.get_recent_messages("a", 2, session=session)

        def entries(*items: tuple[str, str]) -> list[dict[str, str]]:
            return [{"role": role, "content": content} for role, content in items]

        self.assertEqual(a_history, entries(("user", "一"), ("user", "二"), ("assistant", "回复")))
        self.assertEqual(b_history, entries(("user", "别的租户"), ("assistant", "别的回复")))
        self.assertEqual(limited, entries(("user", "二"), ("assistant", "回复")))

    async def test_sent_item_enters_history(self) -> None:
        async with self.maker() as session:
            turn = ChatTurn(tenant="tenant", turn_key="turn-1", status="generating")
            turn_id = await self.repo.upsert_turn(turn, session=session)
            item_id = await self.repo.append_turn_item(
                ChatTurnItem(
                    tenant="tenant",
                    turn_id=turn_id,
                    item_seq=1,
                    role="assistant",
                    content="回复",
                    visible=False,
                    send_status="pending",
                ),
                session=session,
            )
            await session.commit()
            before = await self.repo.get_recent_messages("tenant", 10, session=session)
            first = await self.repo.mark_turn_item_sent("tenant", item_id, "pmid", session=session)
            second = await self.repo.mark_turn_item_sent("tenant", item_id, "pmid", session=session)
            await session.commit()
            after = await self.repo.get_recent_messages("tenant", 10, session=session)

        self.assertEqual(before, [])
        self.assertEqual((first, second), (True, False))
        self.assertEqual(after, [{"role": "assistant", "content": "回复"}])

    async def test_clear_removes_rows_and_increments_generation(self) -> None:
        async with self.maker() as session:
            await self._message(session, "tenant", "msg:1", "一", 1)
            await self._assistant(session, "tenant", "回复", 2)
            await session.commit()
            first = await self.repo.clear_tenant("tenant", session=session)
            await session.commit()

        async with self.maker() as session:
            second = await self.repo.clear_tenant("tenant", session=session)
            state = await self.repo.get_session_state("tenant", session=session)
            messages = (await session.execute(select(ChatMessage))).scalars().all()
            turns = (await session.execute(select(ChatTurn))).scalars().all()
            items = (await session.execute(select(ChatTurnItem))).scalars().all()

        self.assertEqual((first, second), (1, 2))
        self.assertEqual(state.generation, 2)
        self.assertIsNotNone(state.cleared_at)
        self.assertEqual((messages, turns, items), ([], [], []))


class HydrateTests(unittest.IsolatedAsyncioTestCase):
    """进程内缓存从数据库恢复的行为。"""

    def setUp(self) -> None:
        self.store = ChatSessionStore()
        self.service = ChatService(self.store)
        self.addCleanup(memory_state.mark_ready)

    async def test_restores_once_and_respects_limit(self) -> None:
        history = [{"role": "user", "content": f"消息{index}"} for index in range(5)]
        restore = AsyncMock(return_value=history)
        with (
            patch.object(chat_repository, "get_recent_messages", new=restore),
            patch.object(memory_state, "is_ready", return_value=True),
        ):
            await self.service._hydrate("tenant", 3)
            await self.service._hydrate("tenant", 3)

        self.assertEqual(self.store.get("tenant"), history[-3:])
        restore.assert_awaited_once_with("tenant", 3)

    async def test_does_not_overwrite_live_messages(self) -> None:
        self.store.append("tenant", {"role": "user", "content": "新消息"})
        restore = AsyncMock(return_value=[{"role": "user", "content": "旧消息"}])
        with (
            patch.object(chat_repository, "get_recent_messages", new=restore),
            patch.object(memory_state, "is_ready", return_value=True),
        ):
            await self.service._hydrate("tenant", 10)

        self.assertEqual(self.store.get("tenant"), [{"role": "user", "content": "新消息"}])
        self.assertTrue(self.store.is_hydrated("tenant"))
        restore.assert_not_awaited()

    async def test_failure_retries_next_time(self) -> None:
        failing = AsyncMock(side_effect=RuntimeError("db down"))
        with (
            patch.object(chat_repository, "get_recent_messages", new=failing),
            patch.object(memory_state, "is_ready", return_value=True),
        ):
            await self.service._hydrate("tenant", 10)
        self.assertFalse(self.store.is_hydrated("tenant"))

        restore = AsyncMock(return_value=[{"role": "user", "content": "恢复"}])
        with (
            patch.object(chat_repository, "get_recent_messages", new=restore),
            patch.object(memory_state, "is_ready", return_value=True),
        ):
            await self.service._hydrate("tenant", 10)
        self.assertEqual(self.store.get("tenant"), [{"role": "user", "content": "恢复"}])

    async def test_degraded_memory_skips_database(self) -> None:
        restore = AsyncMock()
        with (
            patch.object(chat_repository, "get_recent_messages", new=restore),
            patch.object(memory_state, "is_ready", return_value=False),
        ):
            await self.service._hydrate("tenant", 10)

        restore.assert_not_awaited()
        self.assertTrue(self.store.is_hydrated("tenant"))


class TurnPersistenceTests(unittest.IsolatedAsyncioTestCase):
    """轮次落库字段与生成/发送分离。"""

    def setUp(self) -> None:
        self.service = ChatService(ChatSessionStore())
        self.addCleanup(memory_state.mark_ready)

    async def test_turn_start_persists_identity_and_source(self) -> None:
        request = _request(platform="onebot11", event_id="event-1", message_id="message-1", reply_to_message_id="0")
        with (
            patch.object(memory_state, "is_ready", return_value=True),
            patch.object(chat_repository, "upsert_turn", new=AsyncMock(return_value=7)) as upsert,
            patch.object(chat_repository, "append_message", new=AsyncMock(return_value=9)) as append,
            patch.object(chat_repository, "append_turn_item", new=AsyncMock(return_value=11)),
        ):
            self.assertEqual(await self.service._persist_turn_start(request, "用户: 你好", 3), (7, 9))

        turn = upsert.await_args.args[0]
        message = append.await_args.args[0]
        self.assertEqual(turn.turn_key, "turn-1")
        self.assertEqual(turn.generation, 3)
        self.assertEqual(json.loads(turn.trigger_message_ids), ["message-1"])
        self.assertEqual(json.loads(turn.author_ids), ["user"])
        self.assertEqual(message.dedupe_key, "msg:1")
        self.assertEqual(message.platform, "onebot11")
        self.assertEqual((message.platform_event_id, message.platform_message_id), ("event-1", "message-1"))
        self.assertIsNone(message.occurred_at.tzinfo)

    async def test_generated_reply_is_not_visible_until_sent(self) -> None:
        request = _request()
        response = LLMResponse(content="回复正文")
        with (
            patch.object(memory_state, "is_ready", return_value=True),
            patch.object(chat_repository, "update_turn", new=AsyncMock()) as update_turn,
            patch.object(chat_repository, "update_message_content", new=AsyncMock()) as update_message,
            patch.object(chat_repository, "append_turn_item", new=AsyncMock(return_value=12)) as append,
        ):
            item_id = await self.service._persist_turn_end(
                request, (7, 9), response, "provider", "用户: 你好", [], ["回复正文"]
            )

        self.assertEqual(item_id, [12])
        self.assertEqual(update_turn.await_args.kwargs["status"], "generated")
        self.assertEqual(update_turn.await_args.kwargs["generated_text"], "回复正文")
        item = append.await_args.args[0]
        self.assertFalse(item.visible)
        self.assertEqual((item.item_seq, item.part_index), (1, 0))
        self.assertEqual(item.send_status, "pending")
        update_message.assert_not_awaited()

    async def test_failure_marks_turn_failed(self) -> None:
        request = _request()
        with (
            patch.object(memory_state, "is_ready", return_value=True),
            patch.object(chat_repository, "update_turn", new=AsyncMock()) as update_turn,
        ):
            await self.service._mark_turn_failed(request, RuntimeError("boom"))

        self.assertEqual(update_turn.await_args.kwargs["status"], "failed")
        self.assertIn("RuntimeError: boom", update_turn.await_args.kwargs["error"])

    async def test_mark_sent_uses_turn_item_id(self) -> None:
        request = _request()
        response = ChatResponse(text="回复", metadata={"turn_item_ids": [5]})
        with (
            patch.object(memory_state, "is_ready", return_value=True),
            patch.object(chat_repository, "mark_turn_item_sent", new=AsyncMock()) as mark,
        ):
            await self.service.mark_sent(request, response, 0, "pmid")

        mark.assert_awaited_once_with("tenant", 5, "pmid")


class RestartContinuityTests(unittest.IsolatedAsyncioTestCase):
    """真实服务与数据库走完一轮后，新实例能恢复上下文。"""

    async def asyncSetUp(self) -> None:
        await base_models.init_database()
        await migrate.ensure_schema()
        memory_state.mark_ready()
        self.addCleanup(memory_state.mark_ready)
        self.tenant = f"restart-{uuid4().hex}"
        self.addAsyncCleanup(chat_repository.clear_tenant, self.tenant)

    async def test_reply_survives_restart(self) -> None:
        request = _request(
            session_id=self.tenant,
            group_id=None,
            user_name="Alice",
            turn_id=uuid4().hex,
            dedupe_key=f"msg:{uuid4().hex}",
            trigger_kind="private",
        )
        with (
            patch.object(service_module, "get_config_manager", return_value=_FakeConfig()),
            patch.object(provider_manager, "lease", _fake_lease),
        ):
            response = await ChatService(ChatSessionStore()).process(request)
            self.assertEqual(response.text, "回复")
            await ChatService(ChatSessionStore()).mark_sent(request, response)

        store = ChatSessionStore()
        await ChatService(store)._hydrate(self.tenant, 10)
        self.assertEqual(
            store.get(self.tenant),
            [
                {"role": "user", "content": "Alice(user): 你好"},
                {"role": "assistant", "content": "回复"},
            ],
        )


class EmptyAndFailedSendTests(unittest.IsolatedAsyncioTestCase):
    """空响应与发送失败的落库字段。"""

    def setUp(self) -> None:
        self.service = ChatService(ChatSessionStore())
        self.addCleanup(memory_state.mark_ready)

    async def test_empty_response_marks_failed_without_item(self) -> None:
        request = _request()
        with (
            patch.object(memory_state, "is_ready", return_value=True),
            patch.object(chat_repository, "update_turn", new=AsyncMock()) as update_turn,
            patch.object(chat_repository, "append_turn_item", new=AsyncMock()) as append,
        ):
            item_id = await self.service._persist_turn_end(
                request, (7, 9), LLMResponse(content=""), "p", "用户: 你好", [], []
            )

        self.assertIsNone(item_id)
        self.assertEqual(update_turn.await_args.kwargs["status"], "failed")
        self.assertEqual(update_turn.await_args.kwargs["error"], "空响应")
        append.assert_not_awaited()

    async def test_mark_sending_then_failure_updates_statuses(self) -> None:
        request = _request()
        response = ChatResponse(text="回复", metadata={"turn_item_ids": [5]})
        with (
            patch.object(memory_state, "is_ready", return_value=True),
            patch.object(chat_repository, "update_turn_item", new=AsyncMock()) as update_item,
            patch.object(chat_repository, "update_turn", new=AsyncMock()) as update_turn,
        ):
            await self.service.mark_sending(request, response)
            await self.service.mark_send_failed(request, response, RuntimeError("net"))

        self.assertEqual([call.kwargs["send_status"] for call in update_item.await_args_list], ["sending", "failed"])
        self.assertEqual([call.kwargs["status"] for call in update_turn.await_args_list], ["sending", "failed"])
        self.assertIn("发送失败", update_turn.await_args.kwargs["error"])
        self.assertEqual(self.service.session_store.get("tenant"), [])

    async def test_mark_sent_appends_reply_once(self) -> None:
        request = _request()
        response = ChatResponse(text="回复", metadata={"turn_item_ids": [5]})
        self.service.session_store.append("tenant", {"role": "user", "content": "你好"})
        mark = AsyncMock(side_effect=[True, False])
        with (
            patch.object(memory_state, "is_ready", return_value=True),
            patch.object(chat_repository, "mark_turn_item_sent", new=mark),
            patch.object(service_module, "get_config_manager", return_value=_FakeConfig()),
        ):
            await self.service.mark_sent(request, response)
            await self.service.mark_sent(request, response)

        self.assertEqual(
            self.service.session_store.get("tenant"),
            [{"role": "user", "content": "你好"}, {"role": "assistant", "content": "回复"}],
        )
        self.assertEqual(mark.await_count, 2)


class SendStateTests(unittest.IsolatedAsyncioTestCase):
    """真实服务下的空响应、发送失败与清空失效。"""

    async def asyncSetUp(self) -> None:
        await base_models.init_database()
        await migrate.ensure_schema()
        memory_state.mark_ready()
        self.addCleanup(memory_state.mark_ready)
        self.tenant = f"send-{uuid4().hex}"
        self.addAsyncCleanup(chat_repository.clear_tenant, self.tenant)

    def _new_request(self) -> ChatRequest:
        return _request(
            session_id=self.tenant,
            group_id=None,
            user_name="Alice",
            turn_id=uuid4().hex,
            dedupe_key=f"msg:{uuid4().hex}",
            trigger_kind="private",
        )

    async def _process(self, request: ChatRequest, store: ChatSessionStore, lease: object) -> ChatResponse:
        with (
            patch.object(service_module, "get_config_manager", return_value=_FakeConfig()),
            patch.object(provider_manager, "lease", lease),
        ):
            return await ChatService(store).process(request)

    async def _turn(self, request: ChatRequest) -> ChatTurn:
        async with base_models.async_maker() as session:
            rows = await ChatTurn.select_rows(tenant=self.tenant, turn_key=request.turn_id, session=session)
        return rows[0]

    async def test_failed_send_stays_out_of_history(self) -> None:
        request = self._new_request()
        store = ChatSessionStore()
        response = await self._process(request, store, _fake_lease)
        service = ChatService(store)
        await service.mark_sending(request, response)
        await service.mark_send_failed(request, response, RuntimeError("net down"))

        user = {"role": "user", "content": "Alice(user): 你好"}
        self.assertEqual(store.get(self.tenant), [user])
        self.assertEqual((await self._turn(request)).status, "failed")
        self.assertEqual(await chat_repository.get_recent_messages(self.tenant, 10), [user])

    async def test_empty_response_is_failed_and_not_sent(self) -> None:
        request = self._new_request()
        store = ChatSessionStore()

        class _EmptyProvider:
            async def chat(self, messages: list[dict[str, object]], tools: object = None) -> LLMResponse:
                return LLMResponse(content="  ")

        @asynccontextmanager
        async def empty_lease(_name: str):
            yield _EmptyProvider()

        response = await self._process(request, store, empty_lease)

        self.assertEqual(response.text, "  ")
        self.assertNotIn("turn_item_ids", response.metadata)
        turn = await self._turn(request)
        self.assertEqual((turn.status, turn.error), ("failed", "空响应"))
        self.assertEqual(
            await chat_repository.get_recent_messages(self.tenant, 10),
            [{"role": "user", "content": "Alice(user): 你好"}],
        )

    async def test_clear_during_generation_aborts_turn(self) -> None:
        request = self._new_request()
        store = ChatSessionStore()
        tenant = self.tenant

        class _ClearingProvider:
            async def chat(self, messages: list[dict[str, object]], tools: object = None) -> LLMResponse:
                store.clear(tenant)
                return LLMResponse(content="回复")

        @asynccontextmanager
        async def clearing_lease(_name: str):
            yield _ClearingProvider()

        response = await self._process(request, store, clearing_lease)

        self.assertNotIn("turn_item_ids", response.metadata)
        self.assertFalse(ChatService(store).is_current(self.tenant, response.epoch))
        self.assertEqual((await self._turn(request)).status, "aborted")
        self.assertEqual(
            await chat_repository.get_recent_messages(self.tenant, 10),
            [{"role": "user", "content": "Alice(user): 你好"}],
        )

    async def test_duplicate_send_is_recorded_once(self) -> None:
        request = self._new_request()
        store = ChatSessionStore()
        response = await self._process(request, store, _fake_lease)
        service = ChatService(store)
        with patch.object(service_module, "get_config_manager", return_value=_FakeConfig()):
            await service.mark_sending(request, response)
            await service.mark_sent(request, response, 0, "pmid")
            await service.mark_sent(request, response, 0, "pmid")

        expected = [
            {"role": "user", "content": "Alice(user): 你好"},
            {"role": "assistant", "content": "回复"},
        ]
        self.assertEqual(store.get(self.tenant), expected)
        self.assertEqual(await chat_repository.get_recent_messages(self.tenant, 10), expected)
        self.assertEqual((await self._turn(request)).status, "sent")

    async def test_same_user_new_trigger_aborts_before_generation(self) -> None:
        request = self._new_request()
        store = ChatSessionStore()
        request.trigger_seq = store.next_seq(self.tenant, request.user_id)
        request.accepted_epoch = store.epoch(self.tenant)
        store.next_seq(self.tenant, request.user_id)

        response = await self._process(request, store, _fake_lease)

        self.assertEqual(response.text, "")
        self.assertNotIn("turn_item_ids", response.metadata)
        self.assertEqual((await self._turn(request)).status, "aborted")
        self.assertEqual(
            await chat_repository.get_recent_messages(self.tenant, 10),
            [{"role": "user", "content": "Alice(user): 你好"}],
        )

    async def test_other_user_trigger_does_not_abort(self) -> None:
        request = self._new_request()
        store = ChatSessionStore()
        request.trigger_seq = store.next_seq(self.tenant, request.user_id)
        request.accepted_epoch = store.epoch(self.tenant)
        store.next_seq(self.tenant, "other")

        response = await self._process(request, store, _fake_lease)

        self.assertEqual(response.text, "回复")
        self.assertIn("turn_item_ids", response.metadata)

    async def test_new_trigger_during_generation_voids_reply(self) -> None:
        request = self._new_request()
        store = ChatSessionStore()
        tenant, user_id = self.tenant, request.user_id
        request.trigger_seq = store.next_seq(tenant, user_id)
        request.accepted_epoch = store.epoch(tenant)

        class _InterruptingProvider:
            async def chat(self, messages: list[dict[str, object]], tools: object = None) -> LLMResponse:
                store.next_seq(tenant, user_id)
                return LLMResponse(content="回复")

        @asynccontextmanager
        async def interrupting_lease(_name: str):
            yield _InterruptingProvider()

        response = await self._process(request, store, interrupting_lease)

        self.assertEqual(response.text, "回复")
        self.assertNotIn("turn_item_ids", response.metadata)
        self.assertEqual((await self._turn(request)).status, "aborted")
        self.assertEqual(
            await chat_repository.get_recent_messages(self.tenant, 10),
            [{"role": "user", "content": "Alice(user): 你好"}],
        )

    async def test_cleared_queued_turn_is_dropped(self) -> None:
        request = self._new_request()
        store = ChatSessionStore()
        request.trigger_seq = store.next_seq(self.tenant, request.user_id)
        request.accepted_epoch = store.epoch(self.tenant)
        store.clear(self.tenant)

        response = await self._process(request, store, _fake_lease)

        self.assertEqual(response.text, "")
        self.assertEqual(await chat_repository.get_recent_messages(self.tenant, 10), [])

    async def test_abort_before_send_voids_turn_item(self) -> None:
        request = self._new_request()
        store = ChatSessionStore()
        request.trigger_seq = store.next_seq(self.tenant, request.user_id)
        request.accepted_epoch = store.epoch(self.tenant)
        response = await self._process(request, store, _fake_lease)
        service = ChatService(store)
        await service.mark_sending(request, response)
        await service.mark_send_aborted(request, response)

        self.assertEqual((await self._turn(request)).status, "aborted")
        async with base_models.async_maker() as session:
            item = (
                await ChatTurnItem.select_rows(
                    tenant=self.tenant, id=response.metadata["turn_item_ids"][0], session=session
                )
            )[0]
        self.assertEqual((item.send_status, item.visible), ("skipped", False))
        self.assertEqual(
            await chat_repository.get_recent_messages(self.tenant, 10),
            [{"role": "user", "content": "Alice(user): 你好"}],
        )


class _RecordingMatcher:
    """记录发送内容，可在指定序号抛错。"""

    def __init__(self, fail_at: int | None = None) -> None:
        self.sent: list[str] = []
        self.fail_at = fail_at

    async def send(self, text: str) -> SimpleNamespace:
        if self.fail_at is not None and len(self.sent) == self.fail_at:
            raise RuntimeError("net down")
        await asyncio.sleep(0)
        self.sent.append(text)
        return SimpleNamespace(message_id=f"pmid-{len(self.sent)}")


_LONG_REPLY = "第一段。" * 100


class SplitSendTests(unittest.IsolatedAsyncioTestCase):
    """长回复拆分、分段失败与同租户串行发送。"""

    async def asyncSetUp(self) -> None:
        await base_models.init_database()
        await migrate.ensure_schema()
        memory_state.mark_ready()
        self.addCleanup(memory_state.mark_ready)
        self.tenant = f"split-{uuid4().hex}"
        self.addAsyncCleanup(chat_repository.clear_tenant, self.tenant)

    def _new_request(self, *, user_id: str = "user", text: str = "你好") -> ChatRequest:
        return _request(
            session_id=self.tenant,
            user_id=user_id,
            group_id=None,
            user_name=user_id.title(),
            text=text,
            turn_id=uuid4().hex,
            dedupe_key=f"msg:{uuid4().hex}",
            trigger_kind="private",
        )

    @staticmethod
    def _lease_for(text: str):
        @asynccontextmanager
        async def lease(_name: str):
            class _Provider:
                async def chat(self, messages: list[dict[str, object]], tools: object = None) -> LLMResponse:
                    return LLMResponse(content=text)

            yield _Provider()

        return lease

    async def _send(
        self, request: ChatRequest, store: ChatSessionStore, matcher: object, text: str = _LONG_REPLY
    ) -> bool:
        with (
            patch.object(service_module, "get_config_manager", return_value=_FakeConfig()),
            patch.object(provider_manager, "lease", self._lease_for(text)),
            patch.object(service_module, "part_delay", return_value=0.0),
        ):
            return await ChatService(store).process_and_send(request, ToolRuntime(matcher=matcher))

    async def _turn(self, request: ChatRequest) -> ChatTurn:
        async with base_models.async_maker() as session:
            rows = await ChatTurn.select_rows(tenant=self.tenant, turn_key=request.turn_id, session=session)
        return rows[0]

    async def _assistant_items(self, request: ChatRequest) -> list[ChatTurnItem]:
        turn = await self._turn(request)
        async with base_models.async_maker() as session:
            rows = await ChatTurnItem.select_rows(
                tenant=self.tenant, turn_id=turn.id, role="assistant", session=session
            )
        return sorted(rows, key=lambda row: row.item_seq)

    async def test_long_reply_is_split_and_all_parts_enter_history(self) -> None:
        request = self._new_request()
        store = ChatSessionStore()
        matcher = _RecordingMatcher()

        self.assertTrue(await self._send(request, store, matcher))

        self.assertEqual(matcher.sent, ["第一段。" * 30, "第一段。" * 30, "第一段。" * 40])
        self.assertEqual("".join(matcher.sent), _LONG_REPLY)
        self.assertEqual((await self._turn(request)).status, "sent")
        items = await self._assistant_items(request)
        self.assertEqual([item.part_index for item in items], [0, 1, 2])
        self.assertEqual([item.send_status for item in items], ["sent", "sent", "sent"])
        self.assertEqual([item.visible for item in items], [True, True, True])
        self.assertEqual([item.platform_message_id for item in items], ["pmid-1", "pmid-2", "pmid-3"])
        history = await chat_repository.get_recent_messages(self.tenant, 10)
        self.assertEqual([entry["content"] for entry in history[1:]], matcher.sent)
        self.assertEqual([entry["content"] for entry in store.get(self.tenant)[1:]], matcher.sent)

    async def test_first_segment_failure_marks_turn_failed(self) -> None:
        request = self._new_request()
        store = ChatSessionStore()
        matcher = _RecordingMatcher(fail_at=0)

        self.assertFalse(await self._send(request, store, matcher))

        self.assertEqual(matcher.sent, [])
        self.assertEqual((await self._turn(request)).status, "failed")
        items = await self._assistant_items(request)
        self.assertEqual([item.send_status for item in items], ["failed", "skipped", "skipped"])
        self.assertEqual(
            await chat_repository.get_recent_messages(self.tenant, 10),
            [{"role": "user", "content": "User(user): 你好"}],
        )

    async def test_middle_segment_failure_keeps_sent_history(self) -> None:
        request = self._new_request()
        store = ChatSessionStore()
        matcher = _RecordingMatcher(fail_at=1)

        self.assertTrue(await self._send(request, store, matcher))

        self.assertEqual(matcher.sent, ["第一段。" * 30])
        self.assertEqual((await self._turn(request)).status, "partial")
        items = await self._assistant_items(request)
        self.assertEqual([item.send_status for item in items], ["sent", "failed", "skipped"])
        self.assertEqual([item.visible for item in items], [True, False, False])
        self.assertEqual(
            await chat_repository.get_recent_messages(self.tenant, 10),
            [
                {"role": "user", "content": "User(user): 你好"},
                {"role": "assistant", "content": "第一段。" * 30},
            ],
        )

    async def test_sends_are_serialized_per_tenant(self) -> None:
        store = ChatSessionStore()
        matcher = _RecordingMatcher()
        first = self._new_request(user_id="alice", text="问题甲")
        second = self._new_request(user_id="bob", text="问题乙")
        for request in (first, second):
            request.trigger_seq = store.next_seq(self.tenant, request.user_id)
            request.accepted_epoch = store.epoch(self.tenant)

        class _LabelledProvider:
            async def chat(self, messages: list[dict[str, object]], tools: object = None) -> LLMResponse:
                content = str(messages[-1].get("content", ""))
                mark = "甲" if "甲" in content else "乙"
                return LLMResponse(content=f"{mark * 101}。" * 3)

        @asynccontextmanager
        async def labelled_lease(_name: str):
            yield _LabelledProvider()

        with (
            patch.object(service_module, "get_config_manager", return_value=_FakeConfig()),
            patch.object(provider_manager, "lease", labelled_lease),
            patch.object(service_module, "part_delay", return_value=0.0),
        ):
            await asyncio.gather(
                ChatService(store).process_and_send(first, ToolRuntime(matcher=matcher)),
                ChatService(store).process_and_send(second, ToolRuntime(matcher=matcher)),
            )

        marks = [part[0] for part in matcher.sent]
        self.assertEqual(len(marks), 6)
        self.assertEqual(len(set(marks[:3])), 1)
        self.assertEqual(len(set(marks[3:])), 1)
        self.assertNotEqual(marks[0], marks[3])


class RouterSendTests(unittest.IsolatedAsyncioTestCase):
    """路由只负责调用服务层发送，并按发送结果结束事件。"""

    def _service(self, sent: bool = True) -> SimpleNamespace:
        return SimpleNamespace(
            session_store=ChatSessionStore(),
            ensure_hydrated=AsyncMock(),
            process_and_send=AsyncMock(return_value=sent),
        )

    async def _handle(self, service: SimpleNamespace, matcher: SimpleNamespace) -> None:
        identity = SimpleNamespace(
            session_id="session",
            dedupe_key="msg:1",
            platform="",
            event_id=None,
            message_id=None,
            reply_to_message_id=None,
        )
        with (
            patch.object(router, "chat_service", service),
            patch.object(router, "event_identity", return_value=identity),
            patch.object(router, "adapt_message_event", new=AsyncMock(return_value=_request())),
            patch.object(router.default_runtime_factory, "create", return_value=object()),
        ):
            await router._handle_ai_fallback(SimpleNamespace(), SimpleNamespace(), matcher)

    async def test_finishes_after_successful_send(self) -> None:
        service = self._service()
        matcher = SimpleNamespace(finish=AsyncMock())
        await self._handle(service, matcher)

        service.process_and_send.assert_awaited_once()
        matcher.finish.assert_awaited_once_with()

    async def test_failed_send_does_not_finish(self) -> None:
        service = self._service(sent=False)
        matcher = SimpleNamespace(finish=AsyncMock())
        await self._handle(service, matcher)

        service.process_and_send.assert_awaited_once()
        matcher.finish.assert_not_awaited()

    async def test_processing_error_reports_unavailable(self) -> None:
        service = self._service()
        service.process_and_send = AsyncMock(side_effect=RuntimeError("boom"))
        matcher = SimpleNamespace(finish=AsyncMock())
        await self._handle(service, matcher)

        matcher.finish.assert_awaited_once_with("AI 服务暂时不可用")


class ClearTests(unittest.IsolatedAsyncioTestCase):
    """清空内存、数据库与持久化代次。"""

    async def asyncSetUp(self) -> None:
        await base_models.init_database()
        await migrate.ensure_schema()
        memory_state.mark_ready()
        self.addCleanup(memory_state.mark_ready)
        self.tenant = f"clear-{uuid4().hex}"
        self.addAsyncCleanup(chat_repository.clear_tenant, self.tenant)

    async def test_clear_removes_db_rows_and_aligns_generation(self) -> None:
        request = _request(
            session_id=self.tenant,
            group_id=None,
            user_name="Alice",
            turn_id=uuid4().hex,
            dedupe_key=f"msg:{uuid4().hex}",
            trigger_kind="private",
        )
        store = ChatSessionStore()
        with (
            patch.object(service_module, "get_config_manager", return_value=_FakeConfig()),
            patch.object(provider_manager, "lease", _fake_lease),
        ):
            response = await ChatService(store).process(request)
            await ChatService(store).mark_sent(request, response)
        self.assertEqual(len(store.get(self.tenant)), 2)

        service = ChatService(store)
        await service.clear_session(self.tenant)

        self.assertEqual(store.get(self.tenant), [])
        self.assertEqual(await chat_repository.get_recent_messages(self.tenant, 10), [])
        state = await chat_repository.get_session_state(self.tenant)
        self.assertEqual(state.generation, 1)
        self.assertEqual(store.epoch(self.tenant), 1)
        self.assertFalse(service.is_current(self.tenant, 0))

        restored = ChatSessionStore()
        await ChatService(restored)._hydrate(self.tenant, 10)
        self.assertEqual(restored.get(self.tenant), [])
        self.assertEqual(restored.epoch(self.tenant), 1)

    async def test_clear_invalidates_in_flight_reply(self) -> None:
        service = ChatService(ChatSessionStore())
        epoch = service.session_store.epoch(self.tenant)

        await service.clear_session(self.tenant)

        self.assertFalse(service.is_current(self.tenant, epoch))


class _MutableConfig:
    """用例中可修改配置项的假配置。"""

    def __init__(self, max_history: int) -> None:
        self.max_history = max_history

    def get_system(self) -> dict[str, object]:
        return {
            "chat": {
                "max_history": self.max_history,
                "image_description": False,
                "persona": "",
                "provider": "",
                "enabled_tools": ["__disabled__"],
            }
        }


class MaxHistoryTests(unittest.IsolatedAsyncioTestCase):
    """配置热更新立即影响历史裁剪。"""

    async def test_hot_reload_trims_immediately(self) -> None:
        config = _MutableConfig(10)
        store = ChatSessionStore()
        service = ChatService(store)
        with (
            patch.object(service_module, "get_config_manager", return_value=config),
            patch.object(provider_manager, "lease", _fake_lease),
            patch.object(memory_state, "is_ready", return_value=False),
        ):
            await service.process(_request(session_id="hot", turn_id="turn-1", dedupe_key="msg:1"))
            config.max_history = 1
            await service.process(_request(session_id="hot", turn_id="turn-2", dedupe_key="msg:2"))

        self.assertEqual(len(store.get("hot")), 1)


class BatchPersistenceTests(unittest.IsolatedAsyncioTestCase):
    """合并批次逐条落库，合并内容只作为本轮 user 条目。"""

    async def asyncSetUp(self) -> None:
        await base_models.init_database()
        await migrate.ensure_schema()
        memory_state.mark_ready()
        self.addCleanup(memory_state.mark_ready)
        self.tenant = f"batch-{uuid4().hex}"
        self.addAsyncCleanup(chat_repository.clear_tenant, self.tenant)

    async def test_batch_keeps_raw_messages_and_one_turn(self) -> None:
        first = _request(
            session_id=self.tenant,
            group_id=None,
            user_name="Alice",
            turn_id="turn",
            dedupe_key="msg:1",
            message_id="1",
            trigger_kind="private",
        )
        second = _request(
            session_id=self.tenant,
            group_id=None,
            user_name="Alice",
            turn_id="turn-2",
            dedupe_key="msg:2",
            message_id="2",
            text="在吗",
        )
        batch = PendingBatch(tenant=self.tenant, owner_id="user", requests=[first, second], started=0.0, deadline=0.0)
        merged = batch.merge()
        self.assertTrue(merged.batched)

        store = ChatSessionStore()
        with (
            patch.object(service_module, "get_config_manager", return_value=_FakeConfig()),
            patch.object(provider_manager, "lease", _fake_lease),
        ):
            await ChatService(store).persist_batch(batch.requests)
            response = await ChatService(store).process(merged)

        self.assertEqual(response.text, "回复")
        # 本轮只把合并内容放进内存历史，原始消息留在库里等重启后恢复
        self.assertEqual([entry["content"] for entry in store.get(self.tenant)], ["Alice(user): 你好\n在吗"])
        self.assertEqual(
            await chat_repository.get_recent_messages(self.tenant, 10),
            [
                {"role": "user", "content": "Alice(user): 你好"},
                {"role": "user", "content": "Alice(user): 在吗"},
            ],
        )
        async with base_models.async_maker() as session:
            turns = await ChatTurn.select_rows(tenant=self.tenant, session=session)
            items = await ChatTurnItem.select_rows(tenant=self.tenant, session=session)
        self.assertEqual(len(turns), 1)
        self.assertEqual(json.loads(turns[0].trigger_message_ids), ["1", "2"])
        self.assertEqual(json.loads(turns[0].author_ids), ["user"])
        user_items = [item for item in items if item.role == "user"]
        self.assertEqual(len(user_items), 1)
        self.assertIn("你好\n在吗", user_items[0].content)
        self.assertIsNone(user_items[0].source_message_id)
