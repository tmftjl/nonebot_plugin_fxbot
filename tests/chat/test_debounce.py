"""防抖缓冲与批次合并测试。"""

from __future__ import annotations

import time
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.plugins.nonebot_plugin_fxbot.chat import router
from src.plugins.nonebot_plugin_fxbot.chat.types import MentionRef, ChatRequest
from src.plugins.nonebot_plugin_fxbot.chat.service import ChatService
from src.plugins.nonebot_plugin_fxbot.chat.session import ChatSessionStore
from src.plugins.nonebot_plugin_fxbot.chat.behavior import debounce
from src.plugins.nonebot_plugin_fxbot.chat.session_hooks import SessionPolicy, register_session_policy
from src.plugins.nonebot_plugin_fxbot.chat.behavior.debounce import (
    BATCH_MAX_SECONDS,
    BATCH_MAX_MESSAGES,
    PendingBatch,
    DebounceRegistry,
)

from .support import RouterUnitTests


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


class _NaturalMode(unittest.IsolatedAsyncioTestCase):
    """固定自然档，避免读取本机已保存的配置。"""

    def setUp(self) -> None:
        patcher = patch.object(debounce, "_mode", return_value="natural")
        patcher.start()
        self.addCleanup(patcher.stop)


class BatchMergeTests(_NaturalMode):
    """窗口、上限与合并内容。"""

    async def test_session_policy_skips_only_selected_session_wait(self) -> None:
        self.addCleanup(
            register_session_policy(
                "instant-test", lambda session: SessionPolicy(allow_debounce=False) if session == "instant" else None
            )
        )
        registry = DebounceRegistry()
        instant = registry.create(_request(session_id="instant", trigger_kind="private"))
        ordinary = registry.create(_request(session_id="ordinary", trigger_kind="private"))
        self.assertLessEqual(instant.deadline, debounce._now())
        self.assertGreater(ordinary.deadline, debounce._now())
        instant.accept(_request(session_id="instant", trigger_kind="private"), trigger=True)
        self.assertLessEqual(instant.deadline, debounce._now())
        await asyncio.wait_for(instant.wait(), 0.1)

    def test_mode_profiles(self) -> None:
        self.assertEqual(debounce.WAIT_PROFILES["fast"], debounce.WaitTimes(1.0, 3.0, 1.0, 0.5))
        self.assertEqual(debounce.WAIT_PROFILES["natural"], debounce.WaitTimes(2.0, 5.0, 2.0, 0.5))
        self.assertEqual(debounce.WAIT_PROFILES["steady"], debounce.WaitTimes(4.0, 7.0, 4.0, 2.0))

    def test_quiet_and_active_start(self) -> None:
        now = [100.0]
        registry = DebounceRegistry()
        with patch.object(debounce, "_now", lambda: now[0]):
            batch = registry.create(_request(trigger_kind="private"))
            self.assertEqual(batch.deadline, 102.0)
            registry.pop(batch)
            now[0] = 110.0
            active = registry.create(_request(trigger_kind="private", dedupe_key="msg:2"))
        self.assertEqual(active.deadline, 115.0)

    def test_mention_is_capped_to_one_second(self) -> None:
        with patch.object(debounce, "_now", lambda: 100.0):
            batch = DebounceRegistry().create(_request(trigger_kind="mention"))
        self.assertEqual(batch.deadline, 101.0)

    def test_bare_image_waits_at_least_four_seconds(self) -> None:
        with patch.object(debounce, "_now", lambda: 100.0):
            batch = DebounceRegistry().create(_request(group_id=None, trigger_kind="private", text="", images=["a"]))
        self.assertEqual(batch.deadline, 104.0)

    def test_off_mode_does_not_wait(self) -> None:
        with patch.object(debounce, "_mode", return_value="off"), patch.object(debounce, "_now", lambda: 100.0):
            batch = DebounceRegistry().create(_request())
        self.assertEqual(batch.deadline, 100.0)

    def test_same_author_burst_extends_window(self) -> None:
        now = [100.0]
        registry = DebounceRegistry()
        with patch.object(debounce, "_now", lambda: now[0]):
            batch = registry.create(_request(trigger_kind="private"))
            self.assertEqual(batch.deadline, 102.0)
            now[0] = 101.5
            self.assertTrue(registry.merge_context(_request(dedupe_key="msg:2", message_id="2", text="在吗")))

        self.assertEqual(batch.deadline, 103.5)
        merged = batch.merge()
        self.assertTrue(merged.batched)
        self.assertEqual(merged.text, "你好\n在吗")
        self.assertEqual(merged.source_message_ids, ["1", "2"])
        self.assertEqual(merged.source_author_ids, ["u1"])

    def test_cross_author_context_is_merged_with_identity(self) -> None:
        registry = DebounceRegistry()
        batch = registry.create(_request())
        self.assertTrue(registry.merge_context(_request(user_id="u2", user_name="Bob")))
        self.assertEqual(len(batch.requests), 2)
        self.assertIn("Bob", batch.merge().text)

    def test_cross_author_trigger_is_merged_and_forced(self) -> None:
        registry = DebounceRegistry()
        batch = registry.create(_request())
        merged_batch = registry.merge_trigger(
            _request(user_id="u2", user_name="Bob", dedupe_key="msg:2", message_id="2", text="在吗")
        )
        self.assertIs(merged_batch, batch)
        self.assertTrue(batch.forced)
        self.assertEqual(batch.merge().text, "Alice(u1): 你好\nBob(u2): 在吗")
        self.assertEqual(batch.merge().user_id, "u2")
        self.assertEqual(batch.merge().message_id, "2")

    def test_window_is_capped_at_batch_limit(self) -> None:
        now = [0.0]
        registry = DebounceRegistry()
        with patch.object(debounce, "_now", lambda: now[0]):
            batch = registry.create(_request(trigger_kind="private"))
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

    def test_merged_batch_is_checked_under_latest_trigger_owner(self) -> None:
        """换人触发后，合并轮次按新触发者的序号判断是否被取代。"""
        store = ChatSessionStore()
        registry = DebounceRegistry()
        first = _request(trigger_seq=store.next_seq("tenant", "u1"), trigger_owner="u1")
        batch = registry.create(first)
        second = _request(
            user_id="u2",
            user_name="Bob",
            dedupe_key="msg:2",
            message_id="2",
            trigger_seq=store.next_seq("tenant", "u2"),
            trigger_owner="u2",
        )
        registry.merge_trigger(second)
        merged = batch.merge()
        self.assertEqual(merged.trigger_owner, "u2")
        service = ChatService(store)
        self.assertTrue(service.is_latest(merged))
        store.next_seq("tenant", "u2")
        self.assertFalse(service.is_latest(merged))

    def test_proactive_turn_is_not_invalidated_by_itself(self) -> None:
        """主动轮的序号记在独立归属者下，不能按普通用户去查。"""
        store = ChatSessionStore()
        service = ChatService(store)
        owner = "\0proactive"
        request = _request(
            trigger_kind="proactive",
            proactive=True,
            trigger_seq=store.next_seq("tenant", owner),
            trigger_owner=owner,
        )
        self.assertTrue(service.is_latest(request))
        store.next_seq("tenant", "u1")
        self.assertTrue(service.is_latest(request))

    def test_forced_trigger_wins_over_proactive_in_batch(self) -> None:
        """批次里既有主动又有人 @ 时按普通轮处理，只有纯主动批次才算主动轮。"""
        registry = DebounceRegistry()
        batch = registry.create(_request(trigger_kind="proactive", proactive=True, trigger_seq=1))
        registry.merge_trigger(_request(dedupe_key="msg:2", message_id="2", trigger_seq=2))
        self.assertFalse(batch.merge().proactive)

        pure = DebounceRegistry()
        pure_batch = pure.create(_request(trigger_kind="proactive", proactive=True, trigger_seq=1))
        pure.merge_proactive(_request(dedupe_key="msg:2", message_id="2", trigger_kind="proactive", proactive=True))
        self.assertTrue(pure_batch.merge().proactive)

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


class BatchWaitTests(_NaturalMode):
    """窗口等待与清空唤醒。"""

    async def test_wait_returns_when_deadline_passed(self) -> None:
        batch = PendingBatch(
            tenant="tenant", owner_id="u1", requests=[_request()], started=0, deadline=time.monotonic() - 1
        )
        await asyncio.wait_for(batch.wait(), 1.0)

    async def test_new_message_wakes_and_extends_wait(self) -> None:
        registry = DebounceRegistry()
        waits = debounce.WaitTimes(0.05, 0.05, 0.05, 0.05)
        with patch.dict(debounce.WAIT_PROFILES, {"natural": waits}):
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


class RouterBatchTests(RouterUnitTests):
    """路由只负责落库和投递，防抖与门控由调度器统一完成。"""

    async def _handle(self, service, *, twice=False, epoch=None):
        request = _request()
        identity = SimpleNamespace(
            session_id="tenant",
            event_id="event",
            message_id="1",
            dedupe_key="msg:1",
            platform="test",
            reply_to_message_id=None,
        )
        event = SimpleNamespace(user_id="u1", group_id="group", message_id="1")
        with (
            patch.object(router, "chat_service", service),
            patch.object(router, "event_identity", return_value=identity),
            patch.object(router, "adapt_message_event", AsyncMock(return_value=request)),
            patch.object(router, "_is_to_me", return_value=True),
            patch.object(router, "_focus_or_detach", AsyncMock(return_value=False)),
            patch.object(router, "mention_flags", return_value=(True, False)),
            patch.object(router.emoji_module, "collect_in_background"),
            patch.object(router.default_runtime_factory, "create", return_value=object()),
        ):
            if epoch is not None:
                service.ensure_hydrated.side_effect = lambda *args, **kwargs: service.session_store.set_epoch(
                    "tenant", epoch
                )
            await router._handle_ai_fallback(None, event, SimpleNamespace(finish=AsyncMock()))
            if twice:
                await router._handle_ai_fallback(None, event, SimpleNamespace(finish=AsyncMock()))
        return request

    def service(self):
        return SimpleNamespace(
            session_store=ChatSessionStore(),
            ensure_hydrated=AsyncMock(),
            record_group_message=AsyncMock(),
            process_and_send=AsyncMock(return_value=False),
        )

    async def test_incoming_message_is_persisted_before_dispatch(self):
        service = self.service()
        request = await self._handle(service)
        service.record_group_message.assert_awaited_once()
        self.assertTrue(request.metadata["history_recorded"])
        service.process_and_send.assert_awaited_once()

    async def test_duplicate_delivery_does_not_dispatch_twice(self):
        service = self.service()
        await self._handle(service, twice=True)
        service.record_group_message.assert_awaited_once()
        service.process_and_send.assert_awaited_once()

    async def test_epoch_is_captured_after_hydration(self):
        request = await self._handle(self.service(), epoch=5)
        self.assertEqual(request.accepted_epoch, 5)
