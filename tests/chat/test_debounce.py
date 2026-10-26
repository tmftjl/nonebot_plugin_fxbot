"""防抖缓冲与批次合并测试。"""

from __future__ import annotations

import time
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.plugins.nonebot_plugin_fxbot.chat import router
from src.plugins.nonebot_plugin_fxbot.chat.types import MentionRef, ChatRequest
from src.plugins.nonebot_plugin_fxbot.chat.session import ChatSessionStore
from src.plugins.nonebot_plugin_fxbot.chat.behavior import debounce
from src.plugins.nonebot_plugin_fxbot.chat.behavior.debounce import (
    GROUP_WINDOW,
    PRIVATE_WINDOW,
    BATCH_MAX_SECONDS,
    BATCH_MAX_MESSAGES,
    PendingBatch,
    DebounceRegistry,
)


def _request(**values: object) -> ChatRequest:
    """构造一个群聊触发请求。"""
    fields: dict[str, object] = {
        "session_id": "tenant",
        "user_id": "u1",
        "group_id": "group",
        "text": "你好",
        "turn_id": "turn-1",
        "dedupe_key": "msg:1",
        "trigger_kind": "mention",
        "message_id": "1",
        "user_name": "Alice",
    }
    fields.update(values)
    return ChatRequest(**fields)  # type: ignore[arg-type]


class BatchMergeTests(unittest.TestCase):
    """窗口、上限与合并内容。"""

    def test_windows(self) -> None:
        self.assertEqual(debounce.window_for(_request()), GROUP_WINDOW)
        self.assertEqual(debounce.window_for(_request(group_id=None)), PRIVATE_WINDOW)

    def test_same_author_burst_extends_window(self) -> None:
        now = [100.0]
        registry = DebounceRegistry()
        with patch.object(debounce, "_now", lambda: now[0]):
            batch = registry.create(_request())
            self.assertEqual(batch.deadline, 102.0)
            now[0] = 101.5
            self.assertTrue(registry.merge_context(_request(dedupe_key="msg:2", message_id="2", text="在吗")))

        self.assertEqual(batch.deadline, 103.5)
        merged = batch.merge()
        self.assertTrue(merged.batched)
        self.assertEqual(merged.text, "你好\n在吗")
        self.assertEqual(merged.source_message_ids, ["1", "2"])
        self.assertEqual(merged.source_author_ids, ["u1"])

    def test_cross_author_context_is_not_merged(self) -> None:
        registry = DebounceRegistry()
        batch = registry.create(_request())
        self.assertFalse(registry.merge_context(_request(user_id="u2", user_name="Bob")))
        self.assertEqual(len(batch.requests), 1)

    def test_cross_author_trigger_is_merged_and_forced(self) -> None:
        registry = DebounceRegistry()
        batch = registry.create(_request())
        merged_batch = registry.merge_trigger(
            _request(user_id="u2", user_name="Bob", dedupe_key="msg:2", message_id="2", text="在吗")
        )
        self.assertIs(merged_batch, batch)
        self.assertTrue(batch.forced)
        self.assertEqual(batch.merge().text, "你好\nBob(u2): 在吗")

    def test_window_is_capped_at_batch_limit(self) -> None:
        now = [0.0]
        registry = DebounceRegistry()
        with patch.object(debounce, "_now", lambda: now[0]):
            batch = registry.create(_request())
            now[0] = BATCH_MAX_SECONDS - 1
            batch.accept(_request(dedupe_key="msg:2", message_id="2"), trigger=False)
            self.assertEqual(batch.deadline, BATCH_MAX_SECONDS)
            now[0] = BATCH_MAX_SECONDS - 0.5
            batch.accept(_request(dedupe_key="msg:3", message_id="3"), trigger=False)
            self.assertEqual(batch.deadline, BATCH_MAX_SECONDS)

    def test_message_cap(self) -> None:
        registry = DebounceRegistry()
        batch = registry.create(_request())
        for index in range(2, BATCH_MAX_MESSAGES + 1):
            self.assertTrue(batch.accept(_request(dedupe_key=f"msg:{index}"), trigger=False))
        self.assertTrue(batch.full)
        self.assertFalse(batch.accept(_request(dedupe_key="msg:over"), trigger=False))
        self.assertIsNone(registry.merge_trigger(_request(dedupe_key="msg:over")))
        self.assertFalse(registry.merge_context(_request(dedupe_key="msg:over")))

    def test_single_request_is_returned_unchanged(self) -> None:
        registry = DebounceRegistry()
        request = _request(text=" 你好 ")
        batch = registry.create(request)
        merged = batch.merge()
        self.assertIs(merged, request)
        self.assertFalse(merged.batched)

    def test_images_and_mentions_are_deduplicated(self) -> None:
        registry = DebounceRegistry()
        first = _request(images=["a", "b"], mentions=[MentionRef("u2", "Bob")])
        second = _request(
            dedupe_key="msg:2",
            message_id="2",
            images=["b", "c"],
            mentions=[MentionRef("u2", "Bob"), MentionRef("u3")],
        )
        registry.create(first)
        registry.merge_trigger(second)
        merged = registry.get("tenant").merge()  # type: ignore[union-attr]
        self.assertEqual(merged.images, ["a", "b", "c"])
        self.assertEqual([ref.user_id for ref in merged.mentions], ["u2", "u3"])

    def test_batch_carries_latest_trigger_seq(self) -> None:
        registry = DebounceRegistry()
        batch = registry.create(_request(trigger_seq=1, accepted_epoch=0))
        registry.merge_trigger(_request(dedupe_key="msg:2", message_id="2", trigger_seq=2, accepted_epoch=0))
        merged = batch.merge()
        self.assertEqual(merged.trigger_seq, 2)
        self.assertEqual(merged.accepted_epoch, 0)

    def test_clear_cancels_pending_batch(self) -> None:
        registry = DebounceRegistry()
        batch = registry.create(_request())
        registry.clear("tenant")
        self.assertIsNone(registry.get("tenant"))
        self.assertTrue(batch.cancelled)
        self.assertTrue(batch.changed.is_set())

    def test_registry_isolated_by_tenant(self) -> None:
        registry = DebounceRegistry()
        batch = registry.create(_request())
        self.assertFalse(registry.merge_context(_request(session_id="other")))
        self.assertIs(registry.get("tenant"), batch)


class BatchWaitTests(unittest.IsolatedAsyncioTestCase):
    """窗口等待与清空唤醒。"""

    async def test_wait_returns_when_deadline_passed(self) -> None:
        batch = PendingBatch(
            tenant="tenant", owner_id="u1", requests=[_request()], started=0, deadline=time.monotonic() - 1
        )
        await asyncio.wait_for(batch.wait(), 1.0)

    async def test_new_message_wakes_and_extends_wait(self) -> None:
        registry = DebounceRegistry()
        with patch.object(debounce, "window_for", return_value=0.05):
            batch = registry.create(_request())
            task = asyncio.create_task(batch.wait())
            await asyncio.sleep(0.01)
            batch.accept(_request(dedupe_key="msg:2", message_id="2", text="在吗"), trigger=False)
            await asyncio.wait_for(task, 1.0)
        self.assertEqual(len(batch.requests), 2)

    async def test_cancel_wakes_wait(self) -> None:
        registry = DebounceRegistry()
        batch = registry.create(_request())
        task = asyncio.create_task(batch.wait())
        await asyncio.sleep(0)
        registry.clear("tenant")
        await asyncio.wait_for(task, 1.0)


class RouterBatchTests(unittest.IsolatedAsyncioTestCase):
    """路由只由首个触发消息生成一轮。"""

    def _identity(self) -> SimpleNamespace:
        return SimpleNamespace(
            session_id="tenant",
            dedupe_key="msg:1",
            platform="test",
            event_id=None,
            message_id="1",
            reply_to_message_id=None,
        )

    def _event(self, user_id: str = "u1") -> SimpleNamespace:
        event = SimpleNamespace(user_id=user_id, group_id="group", message=[], text="你好")
        event.get_message = lambda: event.message
        event.get_plaintext = lambda: event.text
        return event

    async def test_context_joins_same_author_batch(self) -> None:
        registry = DebounceRegistry()
        batch = registry.create(_request())
        service = SimpleNamespace(
            session_store=ChatSessionStore(),
            record_group_message=AsyncMock(),
        )
        adapted = _request(dedupe_key="msg:2", message_id="2", text="在吗")
        with (
            patch.object(router, "default_debounce_registry", registry),
            patch.object(router, "chat_service", service),
            patch.object(router, "event_identity", return_value=self._identity()),
            patch.object(router, "adapt_message_event", new=AsyncMock(return_value=adapted)),
            patch.object(router, "collect_mentions", new=AsyncMock(return_value=[])),
        ):
            await router._handle_context(None, self._event())

        self.assertEqual(len(batch.requests), 2)
        self.assertEqual(batch.requests[1].text, "在吗")
        service.record_group_message.assert_not_awaited()

    async def test_context_from_other_author_writes_history_immediately(self) -> None:
        registry = DebounceRegistry()
        batch = registry.create(_request())
        service = SimpleNamespace(
            session_store=ChatSessionStore(),
            record_group_message=AsyncMock(),
        )
        with (
            patch.object(router, "default_debounce_registry", registry),
            patch.object(router, "chat_service", service),
            patch.object(router, "event_identity", return_value=self._identity()),
            patch.object(router, "collect_mentions", new=AsyncMock(return_value=[])),
        ):
            await router._handle_context(None, self._event(user_id="u2"))

        self.assertEqual(len(batch.requests), 1)
        service.record_group_message.assert_awaited_once()

    async def test_owner_flushes_batch_once(self) -> None:
        registry = DebounceRegistry()
        service = SimpleNamespace(
            session_store=ChatSessionStore(),
            persist_batch=AsyncMock(),
            process_and_send=AsyncMock(return_value=False),
            is_current=lambda *_: True,
            ensure_hydrated=AsyncMock(),
        )
        first = _request()
        second = _request(dedupe_key="msg:2", message_id="2", text="在吗")

        async def wait_and_add_context(batch: PendingBatch) -> None:
            batch.accept(second, trigger=False)

        with (
            patch.object(router, "default_debounce_registry", registry),
            patch.object(router, "chat_service", service),
            patch.object(router, "event_identity", return_value=self._identity()),
            patch.object(router, "adapt_message_event", new=AsyncMock(return_value=first)),
            patch.object(PendingBatch, "wait", wait_and_add_context),
            patch.object(router.default_runtime_factory, "create", return_value=object()),
        ):
            await router._handle_ai_fallback(None, self._event(), SimpleNamespace(finish=AsyncMock()))

        service.process_and_send.assert_awaited_once()
        merged = service.process_and_send.await_args.args[0]
        self.assertTrue(merged.batched)
        self.assertEqual(merged.text, "你好\n在吗")
        service.persist_batch.assert_awaited_once_with([first, second])

    async def test_epoch_is_captured_after_hydration(self) -> None:
        registry = DebounceRegistry()
        store = ChatSessionStore()

        async def hydrate(_session_id: str) -> None:
            store.set_epoch("tenant", 5)

        service = SimpleNamespace(
            session_store=store,
            process_and_send=AsyncMock(return_value=False),
            is_current=lambda *_: True,
            is_latest=lambda *_: True,
            ensure_hydrated=hydrate,
        )
        first = _request()
        with (
            patch.object(router, "default_debounce_registry", registry),
            patch.object(router, "chat_service", service),
            patch.object(router, "event_identity", return_value=self._identity()),
            patch.object(router, "adapt_message_event", new=AsyncMock(return_value=first)),
            patch.object(PendingBatch, "wait", new=AsyncMock()),
            patch.object(router.default_runtime_factory, "create", return_value=object()),
        ):
            await router._handle_ai_fallback(None, self._event(), SimpleNamespace(finish=AsyncMock()))

        self.assertEqual(first.accepted_epoch, 5)
        self.assertEqual(first.trigger_seq, 1)

    async def test_second_trigger_merges_without_processing(self) -> None:
        registry = DebounceRegistry()
        service = SimpleNamespace(
            session_store=ChatSessionStore(), process_and_send=AsyncMock(), ensure_hydrated=AsyncMock()
        )
        first = _request()
        second = _request(dedupe_key="msg:2", message_id="2", text="在吗")
        registry.create(first)
        identity = self._identity()
        identity.dedupe_key = "msg:2"

        with (
            patch.object(router, "default_debounce_registry", registry),
            patch.object(router, "chat_service", service),
            patch.object(router, "event_identity", return_value=identity),
            patch.object(router, "adapt_message_event", new=AsyncMock(return_value=second)),
            patch.object(router.default_runtime_factory, "create", return_value=object()),
        ):
            await router._handle_ai_fallback(None, self._event("u2"), SimpleNamespace(finish=AsyncMock()))

        self.assertEqual(len(registry.get("tenant").requests), 2)  # type: ignore[union-attr]
        service.process_and_send.assert_not_awaited()

    async def test_clear_during_wait_skips_reply(self) -> None:
        registry = DebounceRegistry()
        service = SimpleNamespace(
            session_store=ChatSessionStore(), process_and_send=AsyncMock(), ensure_hydrated=AsyncMock()
        )

        async def wait_then_clear(batch: PendingBatch) -> None:
            registry.clear(batch.tenant)

        with (
            patch.object(router, "default_debounce_registry", registry),
            patch.object(router, "chat_service", service),
            patch.object(router, "event_identity", return_value=self._identity()),
            patch.object(router, "adapt_message_event", new=AsyncMock(return_value=_request())),
            patch.object(PendingBatch, "wait", wait_then_clear),
            patch.object(router.default_runtime_factory, "create", return_value=object()),
        ):
            await router._handle_ai_fallback(None, self._event(), SimpleNamespace(finish=AsyncMock()))

        service.process_and_send.assert_not_awaited()
