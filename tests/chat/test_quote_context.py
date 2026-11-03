"""引用作者和原文随当前消息保留，不能混淆引用目标与回复目标。"""

from __future__ import annotations

import unittest
from uuid import uuid4
from types import SimpleNamespace
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from nonebot.adapters.onebot.v11 import Message, MessageSegment
from src.plugins.nonebot_plugin_fxbot import adapter
from src.plugins.nonebot_plugin_fxbot.db import base_models
from src.plugins.nonebot_plugin_fxbot.chat import message_adapter
from src.plugins.nonebot_plugin_fxbot.chat.types import ChatRequest
from src.plugins.nonebot_plugin_fxbot.chat.memory import state, migrate
from src.plugins.nonebot_plugin_fxbot.utils.cache import cache
from src.plugins.nonebot_plugin_fxbot.chat.service import ChatService
from src.plugins.nonebot_plugin_fxbot.chat.session import ChatSessionStore
from src.plugins.nonebot_plugin_fxbot.chat.replyer.prompt import assemble_messages, build_user_content
from src.plugins.nonebot_plugin_fxbot.chat.message_context import quote_preview, render_message, message_metadata
from src.plugins.nonebot_plugin_fxbot.chat.memory.repository import chat_repository
from src.plugins.nonebot_plugin_fxbot.chat.focus.group_runtime import FocusGroupRuntime
from src.plugins.nonebot_plugin_fxbot.adapter.platforms.onebot11 import OneBotV11MessageAdapter

SNAPSHOT = {"message_id": "10", "sender_id": "3", "sender_name": "张三", "text": "明天全员休息"}


def request() -> ChatRequest:
    """李四引用张三，机器人应回答李四而非张三。"""
    return ChatRequest(
        "quote-test",
        "4",
        "101",
        "这说得对吗",
        "t",
        "msg:41",
        "mention",
        message_id="41",
        user_name="李四",
        reply_to_message_id="10",
        quoted_text=quote_preview(SNAPSHOT),
        metadata={"quote_snapshot": dict(SNAPSHOT)},
    )


def history_entry() -> dict:
    """引用快照属于李四的这条发言，不依赖张三原消息仍在窗口内。"""
    return {
        "role": "user",
        "content": "李四(4): 这说得对吗",
        "_source_ids": ["41"],
        **message_metadata(
            timestamp=datetime(2026, 11, 3, tzinfo=timezone.utc),
            sender_id="4",
            sender_name="李四",
            quote_id="10",
            quote_snapshot=SNAPSHOT,
        ),
    }


class QuoteAdapterTests(unittest.IsolatedAsyncioTestCase):
    """平台事件完整时零查询，缺失时一次查询同时取得作者和原文。"""

    def setUp(self) -> None:
        cache.clear_prefix(("chat_reply_info",))
        self.addCleanup(cache.clear_prefix, ("chat_reply_info",))
        self.platform = OneBotV11MessageAdapter()
        self.bot = SimpleNamespace(self_id="501", adapter=SimpleNamespace(get_name=lambda: "OneBot V11"))
        self.api = self.enterContext(patch.object(self.platform, "_api", AsyncMock()))
        self.enterContext(patch.object(message_adapter, "get_platform_adapter", return_value=self.platform))
        self.enterContext(patch.object(adapter, "get_platform_adapter", return_value=self.platform))

    def event(self, reply=None, message_id=41):
        """构造消息里携带真实引用段的 OneBot 事件。"""
        message = Message([MessageSegment.reply(10), MessageSegment.text("这说得对吗")])
        return SimpleNamespace(
            group_id=101,
            user_id=4,
            message_id=message_id,
            reply=reply,
            sender=SimpleNamespace(user_id=4, nickname="李四", role="member"),
            get_message=lambda: message,
            get_plaintext=lambda: "这说得对吗",
            get_user_id=lambda: "4",
        )

    async def adapt(self, event):
        """排除图片下载和语音，只验证现有消息适配链。"""
        with (
            patch.object(message_adapter, "collect_mentions", AsyncMock(return_value=[])),
            patch.object(message_adapter, "transcribe_event", AsyncMock(return_value="")),
            patch.object(message_adapter, "load_images", AsyncMock(return_value=[])),
        ):
            return await message_adapter.adapt_message_event(self.bot, event, trigger_kind="mention")

    async def test_embedded_reply_needs_no_api_and_is_reused_by_adaptation(self) -> None:
        reply = SimpleNamespace(
            message_id=10,
            sender=SimpleNamespace(user_id=3, card="张三", nickname="旧昵称"),
            message=Message("明天全员休息"),
        )
        event = self.event(reply)
        self.assertEqual(await message_adapter.read_quote_snapshot(self.bot, event), SNAPSHOT)
        current = await self.adapt(event)
        self.assertEqual(current.metadata["quote_snapshot"], SNAPSHOT)
        self.api.assert_not_awaited()

    async def test_missing_reply_fetches_author_and_raw_segments_only_once(self) -> None:
        self.api.return_value = {
            "message_id": 10,
            "group_id": 101,
            "message_type": "group",
            "sender": {"user_id": 3, "nickname": "张三"},
            "message": [{"type": "text", "data": {"text": "明天全员休息"}}],
        }
        event = self.event()
        snapshot = await message_adapter.read_quote_snapshot(self.bot, event)
        self.assertEqual(snapshot, SNAPSHOT)
        self.assertEqual((await self.adapt(event)).metadata["quote_snapshot"], SNAPSHOT)
        self.api.assert_awaited_once_with(self.bot, "get_msg", message_id="10")

    async def test_wrong_group_or_message_does_not_leak_fetched_content(self) -> None:
        for index, fields in enumerate(({"group_id": 102}, {"message_id": 999}, {"message_type": "private"})):
            self.api.return_value = {
                "message_id": 10,
                "group_id": 101,
                "message_type": "group",
                "sender": {"user_id": 3, "nickname": "别处的人"},
                "message": "别处的消息",
                **fields,
            }
            snapshot = await message_adapter.read_quote_snapshot(self.bot, self.event(message_id=50 + index))
            self.assertEqual(snapshot["sender_name"], "")
            self.assertEqual(snapshot["text"], "")

    async def test_failed_fetch_does_not_guess_author_or_repeat_query(self) -> None:
        self.api.side_effect = RuntimeError("unavailable")
        event = self.event()
        first = await message_adapter.read_quote_snapshot(self.bot, event)
        second = await message_adapter.read_quote_snapshot(self.bot, event)
        self.assertEqual(first, second)
        self.assertEqual(quote_preview(first), "[引用 来源未知]")
        self.api.assert_awaited_once()


class QuotePromptTests(unittest.TestCase):
    """原消息不在上下文时仍能知道引用谁，且只展示一次预览。"""

    def test_recorded_current_message_keeps_quote_when_original_is_absent(self) -> None:
        messages = assemble_messages(request(), [history_entry()], "", "", build_user_content(request()))
        self.assertEqual(len(messages), 1)
        content = messages[0]["content"]
        self.assertIn('msg_id="41" quote="10"', content)
        self.assertIn('user="李四"', content)
        self.assertEqual(content.count("[引用 张三(3)] 明天全员休息"), 1)

    def test_unrecorded_current_message_does_not_duplicate_quote(self) -> None:
        current = request()
        messages = assemble_messages(current, [], "", "", build_user_content(current))
        self.assertEqual(str(messages).count("[引用 张三(3)] 明天全员休息"), 1)

    def test_quote_target_is_not_the_message_the_bot_is_answering(self) -> None:
        metadata = message_metadata(
            timestamp=datetime(2026, 11, 3, tzinfo=timezone.utc), quote_id="41", quote_snapshot=SNAPSHOT
        )
        self.assertEqual(metadata["_quote_ids"], ["10"])
        sent = message_metadata(timestamp=datetime(2026, 11, 3, tzinfo=timezone.utc), quote_id="41")
        self.assertEqual(sent["_quote_ids"], ["41"])
        self.assertIsNone(sent["_quote_snapshot"])

    def test_focus_uses_the_same_quote_snapshot_once(self) -> None:
        group = FocusGroupRuntime("quote-focus")
        group._queue_input("quote-test", request(), True)
        content = group._pending["quote-test"][0].messages[0]["content"]
        self.assertIn('user="李四"', content)
        self.assertEqual(content.count("[引用 张三(3)] 明天全员休息"), 1)

    def test_old_history_without_snapshot_remains_unknown(self) -> None:
        item = history_entry()
        item.pop("_quote_snapshot")
        content = render_message(item)["content"]
        self.assertIn('quote="10"', content)
        self.assertNotIn("张三", content)

    def test_preview_is_short_and_single_line(self) -> None:
        text = quote_preview({**SNAPSHOT, "sender_name": "张三\n别的人", "text": "原文" * 500})
        self.assertNotIn("\n", text)
        self.assertLess(len(text), 550)


class QuotePersistenceTests(unittest.IsolatedAsyncioTestCase):
    """引用作者与正文保存在现有 payload 内，不新增原消息或改表。"""

    async def test_missing_original_quote_survives_restart(self) -> None:
        await base_models.init_database()
        await migrate.ensure_schema()
        tenant = f"quote-context:{uuid4().hex}"
        self.addAsyncCleanup(chat_repository.clear_tenant, tenant)
        service = ChatService(ChatSessionStore())
        with (
            patch.object(state, "is_ready", return_value=True),
            patch("src.plugins.nonebot_plugin_fxbot.chat.message_receive.history._track_message", AsyncMock()),
        ):
            await service.record_group_message(
                tenant,
                "4",
                "李四",
                "这说得对吗",
                is_group=True,
                message_id="41",
                dedupe_key="msg:41",
                reply_to_message_id="10",
                quote_snapshot=SNAPSHOT,
            )
        self.assertIsNone(await chat_repository.message_by_identity(tenant, "10"))
        store = ChatSessionStore()
        with patch.object(state, "is_ready", return_value=True):
            await ChatService(store)._hydrate(tenant, 10)
        current = request()
        current.session_id = tenant
        messages = assemble_messages(current, store.get(tenant), "", "", build_user_content(current))
        self.assertIn("[引用 张三(3)] 明天全员休息", str(messages))
        self.assertEqual(str(messages).count("[引用 张三(3)] 明天全员休息"), 1)
        self.assertEqual(await chat_repository.get_recent_messages(f"{tenant}:other", 10), [])
