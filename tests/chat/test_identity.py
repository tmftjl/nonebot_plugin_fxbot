"""消息身份、会话隔离和重复投递测试。"""

from __future__ import annotations

import unittest
from uuid import UUID
from types import SimpleNamespace
from dataclasses import FrozenInstanceError
from unittest.mock import AsyncMock, patch

from src.plugins.nonebot_plugin_fxbot.chat import router, message_adapter
from src.plugins.nonebot_plugin_fxbot.chat.types import ChatRequest
from src.plugins.nonebot_plugin_fxbot.chat.session import ChatSessionStore
from src.plugins.nonebot_plugin_fxbot.adapter.core.bot import PlatformAdapter
from src.plugins.nonebot_plugin_fxbot.adapter.platforms.qq import QQOfficialMessageAdapter
from src.plugins.nonebot_plugin_fxbot.chat.behavior.debounce import PendingBatch, DebounceRegistry
from src.plugins.nonebot_plugin_fxbot.adapter.platforms.onebot11 import OneBotV11MessageAdapter
from src.plugins.nonebot_plugin_fxbot.adapter.platforms.onebot12 import OneBotV12MessageAdapter


def make_bot(self_id: str = "bot", platform: str = "test") -> SimpleNamespace:
    """构造不启动平台连接的 Bot。"""
    return SimpleNamespace(self_id=self_id, adapter=SimpleNamespace(get_name=lambda: platform))


def make_event(**values: object) -> SimpleNamespace:
    """构造带纯文本和原始消息访问器的假事件。"""
    fields = {"user_id": "user", "group_id": "group", "message": [], "text": "你好"}
    fields.update(values)
    event = SimpleNamespace(**fields)
    event.get_message = lambda: event.message
    event.get_plaintext = lambda: event.text
    return event


class PlatformIdentityTests(unittest.TestCase):
    """各平台字段语义测试。"""

    def test_base_defaults(self) -> None:
        self.assertIsNone(PlatformAdapter.event_id(None, SimpleNamespace(id="not-an-event")))
        self.assertIsNone(PlatformAdapter.event_message_id(None, SimpleNamespace(message_id="not-a-message")))

    def test_onebot12_distinct_ids(self) -> None:
        adapter = OneBotV12MessageAdapter()
        event = SimpleNamespace(id="event-12", message_id=120)
        self.assertEqual(adapter.event_id(event), "event-12")
        self.assertEqual(adapter.event_message_id(event), "120")

    def test_onebot11_has_no_event_id(self) -> None:
        adapter = OneBotV11MessageAdapter()
        event = SimpleNamespace(id="not-an-event-id", event_id="also-not-an-event-id", message_id=110)
        self.assertIsNone(adapter.event_id(event))
        self.assertEqual(adapter.event_message_id(event), "110")

    def test_qq_distinct_ids(self) -> None:
        adapter = QQOfficialMessageAdapter()
        event = SimpleNamespace(event_id="qq-event", id="qq-message")
        self.assertEqual(adapter.event_id(event), "qq-event")
        self.assertEqual(adapter.event_message_id(event), "qq-message")
        self.assertIsNone(adapter.event_id(SimpleNamespace(id="qq-message")))


class MessageIdentityTests(unittest.TestCase):
    """身份优先级和稳定性测试。"""

    def setUp(self) -> None:
        self.bot = make_bot()
        self.adapter = OneBotV12MessageAdapter()
        self.adapter_patch = patch.object(message_adapter, "get_platform_adapter", return_value=self.adapter)
        self.adapter_patch.start()
        self.addCleanup(self.adapter_patch.stop)

    def test_message_precedes_event_and_content(self) -> None:
        identity = message_adapter.event_identity(self.bot, make_event(id="event", message_id="message"))
        self.assertEqual(identity.dedupe_key, "msg:message")
        self.assertEqual(identity.event_id, "event")
        self.assertEqual(identity.message_id, "message")
        with self.assertRaises(FrozenInstanceError):
            identity.message_id = "changed"

    def test_event_precedes_content(self) -> None:
        self.assertEqual(message_adapter.event_identity(self.bot, make_event(id="event")).dedupe_key, "event:event")

    def test_content_hash_stable_and_scoped(self) -> None:
        event = make_event()
        identity = message_adapter.event_identity(self.bot, event)
        self.assertRegex(identity.dedupe_key, r"^content:[0-9a-f]{64}$")
        self.assertEqual(identity, message_adapter.event_identity(self.bot, make_event()))
        for changed in (make_event(text="再见"), make_event(user_id="other"), make_event(group_id="other")):
            with self.subTest(event=changed):
                self.assertNotEqual(identity.dedupe_key, message_adapter.event_identity(self.bot, changed).dedupe_key)
        for bot in (make_bot("other"), make_bot(platform="other")):
            self.assertNotEqual(identity.dedupe_key, message_adapter.event_identity(bot, event).dedupe_key)

    def test_image_only_content_hash(self) -> None:
        event = make_event(text="", message=[SimpleNamespace(type="image", data={"url": "https://example.com/a"})])
        first = message_adapter.event_identity(self.bot, event)
        self.assertRegex(first.dedupe_key, r"^content:[0-9a-f]{64}$")
        self.assertEqual(first, message_adapter.event_identity(self.bot, event))
        event.message[0].data["url"] = "https://example.com/b"
        self.assertNotEqual(first.dedupe_key, message_adapter.event_identity(self.bot, event).dedupe_key)

    def test_uuid_fallback_is_not_idempotent(self) -> None:
        event = make_event(text="")
        first = message_adapter.event_identity(self.bot, event).dedupe_key
        second = message_adapter.event_identity(self.bot, event).dedupe_key
        self.assertRegex(first, r"^uuid:[0-9a-f]{32}$")
        self.assertEqual(UUID(hex=first.removeprefix("uuid:")).version, 4)
        self.assertNotEqual(first, second)

    def test_sessions_isolate_bots_and_scenes(self) -> None:
        group = message_adapter.session_id_for_event(self.bot, make_event(group_id="same", user_id="same"))
        other_bot = message_adapter.session_id_for_event(make_bot("other"), make_event(group_id="same", user_id="same"))
        private = message_adapter.session_id_for_event(self.bot, make_event(group_id=None, user_id="same"))
        self.assertNotEqual(group, other_bot)
        self.assertNotEqual(group, private)

    def test_reply_target_is_not_current_message(self) -> None:
        event = make_event(id="event", message_id="current", reply=SimpleNamespace(message_id=42))
        identity = message_adapter.event_identity(self.bot, event)
        self.assertEqual(identity.message_id, "current")
        self.assertEqual(identity.reply_to_message_id, "42")
        self.assertIsNone(message_adapter.event_identity(self.bot, make_event()).reply_to_message_id)

    def test_platform_specific_identity(self) -> None:
        cases = (
            (OneBotV11MessageAdapter(), make_event(id="ignored", message_id=11), None, "11"),
            (QQOfficialMessageAdapter(), make_event(event_id="qq-event", id="qq-message"), "qq-event", "qq-message"),
        )
        for adapter, event, event_id, message_id in cases:
            with self.subTest(adapter=type(adapter).__name__):
                with patch.object(message_adapter, "get_platform_adapter", return_value=adapter):
                    identity = message_adapter.event_identity(self.bot, event)
                self.assertEqual(identity.event_id, event_id)
                self.assertEqual(identity.message_id, message_id)
                self.assertEqual(identity.dedupe_key, f"msg:{message_id}")


class SessionDedupeTests(unittest.TestCase):
    """会话去重容量和回收测试。"""

    def test_repeat_and_session_isolation(self) -> None:
        store = ChatSessionStore()
        self.assertTrue(store.mark_seen("session", "msg:1"))
        self.assertFalse(store.mark_seen("session", "msg:1"))
        self.assertTrue(store.mark_seen("other", "msg:1"))

    def test_clear_removes_seen_keys(self) -> None:
        store = ChatSessionStore()
        store.mark_seen("session", "msg:1")
        store.append("session", {"role": "user", "content": "你好"})
        store.clear("session")
        self.assertEqual(store.get("session"), [])
        self.assertTrue(store.mark_seen("session", "msg:1"))

    def test_trigger_seq_is_per_user_and_session(self) -> None:
        store = ChatSessionStore()
        first = store.next_seq("session", "a")
        second = store.next_seq("session", "b")
        third = store.next_seq("session", "a")
        self.assertEqual((first, second, third), (1, 2, 3))
        self.assertFalse(store.is_latest_seq("session", "a", first))
        self.assertTrue(store.is_latest_seq("session", "a", third))
        self.assertTrue(store.is_latest_seq("session", "b", second))
        self.assertFalse(store.is_latest_seq("session", "a", 0))

        store.clear("session")
        self.assertFalse(store.is_latest_seq("session", "a", third))
        self.assertEqual(store.next_seq("session", "a"), 1)

    def test_fifo_limit_does_not_refresh_duplicates(self) -> None:
        store = ChatSessionStore()
        for index in range(256):
            self.assertTrue(store.mark_seen("session", f"msg:{index}"))
        self.assertFalse(store.mark_seen("session", "msg:0"))
        self.assertTrue(store.mark_seen("session", "msg:256"))
        self.assertFalse(store.mark_seen("session", "msg:1"))
        self.assertTrue(store.mark_seen("session", "msg:0"))

    def test_reclaimed_session_removes_seen_keys(self) -> None:
        store = ChatSessionStore()
        with patch("src.plugins.nonebot_plugin_fxbot.chat.session._MAX_SESSIONS", 1):
            store.mark_seen("old", "msg:1")
            store.mark_seen("new", "msg:1")
            self.assertNotIn("old", store._seen)
            self.assertTrue(store.mark_seen("old", "msg:1"))


class RequestAndRouterTests(unittest.IsolatedAsyncioTestCase):
    """请求字段和路由去重集成测试。"""

    async def test_request_identity_and_unique_turn(self) -> None:
        event = make_event(id="event", message_id="message", reply=SimpleNamespace(message_id="original"))
        with (
            patch.object(message_adapter, "get_platform_adapter", return_value=OneBotV12MessageAdapter()),
            patch.object(message_adapter, "_reply_info", new=AsyncMock(return_value=None)),
            patch.object(message_adapter, "collect_mentions", new=AsyncMock(return_value=[])),
        ):
            first = await message_adapter.adapt_message_event(make_bot(), event, trigger_kind="mention")
            second = await message_adapter.adapt_message_event(make_bot(), event, trigger_kind="mention")
        self.assertEqual(first.trigger_kind, "mention")
        self.assertEqual(first.event_id, "event")
        self.assertEqual(first.message_id, "message")
        self.assertEqual(first.reply_to_message_id, "original")
        self.assertEqual(first.dedupe_key, second.dedupe_key)
        self.assertNotEqual(first.turn_id, second.turn_id)
        self.assertEqual(UUID(hex=first.turn_id).version, 4)

    async def test_router_dedupe_and_trigger_kinds(self) -> None:
        for group_id, to_me, kind in (
            (None, False, "private"),
            ("group", True, "mention"),
            ("group", False, "persona"),
        ):
            with self.subTest(kind=kind):
                store = ChatSessionStore()
                event = make_event(group_id=group_id, to_me=to_me, message_id="message")
                matcher = SimpleNamespace(finish=AsyncMock())
                service = SimpleNamespace(
                    session_store=store,
                    process_and_send=AsyncMock(return_value=False),
                    ensure_hydrated=AsyncMock(),
                )
                adapt = AsyncMock(
                    return_value=ChatRequest(
                        session_id="session",
                        user_id="user",
                        group_id=group_id,
                        text="你好",
                        turn_id="turn",
                        dedupe_key="msg:message",
                        trigger_kind=kind,
                    )
                )
                with (
                    patch.object(message_adapter, "get_platform_adapter", return_value=OneBotV12MessageAdapter()),
                    patch.object(router, "chat_service", service),
                    patch.object(router, "adapt_message_event", adapt),
                    patch.object(router, "default_debounce_registry", DebounceRegistry()),
                    patch.object(PendingBatch, "wait", new=AsyncMock()),
                    patch.object(router.default_runtime_factory, "create", return_value=object()),
                ):
                    await router._handle_ai_fallback(make_bot(), event, matcher)
                    await router._handle_ai_fallback(make_bot(), event, matcher)
                adapt.assert_awaited_once_with(unittest.mock.ANY, event, trigger_kind=kind)
                service.process_and_send.assert_awaited_once()
                matcher.finish.assert_not_awaited()

    async def test_context_cannot_also_write_ai_history(self) -> None:
        event = make_event(message_id="message")
        service = SimpleNamespace(
            session_store=ChatSessionStore(), record_group_message=AsyncMock(), process_and_send=AsyncMock()
        )
        with (
            patch.object(message_adapter, "get_platform_adapter", return_value=OneBotV12MessageAdapter()),
            patch.object(router, "chat_service", service),
            patch.object(router, "default_debounce_registry", DebounceRegistry()),
            patch.object(router, "collect_mentions", new=AsyncMock(return_value=[])),
        ):
            await router._handle_context(make_bot(), event)
            await router._handle_context(make_bot(), event)
            await router._handle_ai_fallback(make_bot(), event, SimpleNamespace(finish=AsyncMock()))
        service.record_group_message.assert_awaited_once()
        service.process_and_send.assert_not_awaited()
