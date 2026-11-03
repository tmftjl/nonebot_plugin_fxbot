"""按 MaiBot 的真实消息上下文验证新旧消息、触发身份和撤回后持久化。"""

from __future__ import annotations

import json
import unittest
from uuid import uuid4
from types import SimpleNamespace
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch

from src.plugins.nonebot_plugin_fxbot.db import base_models
from src.plugins.nonebot_plugin_fxbot.chat.types import ChatRequest
from src.plugins.nonebot_plugin_fxbot.chat.memory import migrate, lifecycle
from src.plugins.nonebot_plugin_fxbot.chat.service import ChatService
from src.plugins.nonebot_plugin_fxbot.chat.session import ChatSessionStore
from src.plugins.nonebot_plugin_fxbot.chat.heart_flow import plan_flow
from src.plugins.nonebot_plugin_fxbot.chat.memory.models import ChatTurn, ChatMessage, ChatTurnItem
from src.plugins.nonebot_plugin_fxbot.chat.replyer.prompt import protocol_history, assemble_messages
from src.plugins.nonebot_plugin_fxbot.chat.message_context import render_message, message_metadata, restore_reference
from src.plugins.nonebot_plugin_fxbot.chat.behavior.debounce import PendingBatch
from src.plugins.nonebot_plugin_fxbot.chat.memory.repository import chat_repository
from src.plugins.nonebot_plugin_fxbot.chat.message_receive.history import hydrate


def request(identity: str = "new", **values) -> ChatRequest:
    """构造可以区分来源消息与逻辑轮次的真实请求。"""
    return ChatRequest(
        session_id="context-test",
        user_id="3226795632",
        group_id="group",
        text="答非所问 你在说什么",
        turn_id=uuid4().hex,
        dedupe_key=f"msg:{identity}",
        message_id=identity,
        trigger_kind="mention",
        user_name="友利奈绪",
        **values,
    )


class MessageContextTests(unittest.TestCase):
    """验证实际传给模型的信息而不是只验证保存的配置。"""

    def test_message_has_original_time_speaker_quote_and_at(self) -> None:
        entry = {
            "role": "user",
            "content": "友利奈绪(3226795632): 答非所问 你在说什么",
            "_source_ids": ["new"],
            **message_metadata(
                timestamp=datetime(2026, 11, 3, 2, 37, 14, tzinfo=timezone.utc),
                sender_id="3226795632",
                sender_name="友利奈绪",
                quote_id="previous",
                at_bot=True,
            ),
        }
        rendered = render_message(entry, bot_name="小鱼")
        self.assertIn('msg_id="new" quote="previous" time="10:37:14" user="友利奈绪"', rendered["content"])
        self.assertIn("@小鱼\n答非所问 你在说什么", rendered["content"])
        self.assertNotIn("_timestamp", rendered)

    def test_self_reply_is_im_message_not_planner_assistant(self) -> None:
        entry = {
            "role": "assistant",
            "content": "我刚才答偏了",
            "_self_message": True,
            "_source_ids": ["sent"],
            **message_metadata(
                timestamp=datetime(2026, 11, 3, 2, tzinfo=timezone.utc), sender_name="小鱼", quote_id="new"
            ),
        }
        rendered = render_message(entry)
        self.assertEqual(rendered["role"], "user")
        self.assertIn('is_self_message="true"', rendered["content"])
        self.assertIn('quote="new"', rendered["content"])

    def test_batch_keeps_latest_identity_and_its_trigger_metadata(self) -> None:
        old = request("old")
        old.user_id, old.user_name, old.text, old.trigger_kind = "other", "群友", "你要晓得的是", "proactive"
        old.metadata["has_at_bot"] = False
        current = request("new", metadata={"has_at_bot": True})
        merged = PendingBatch("context-test", "other", [old, current], 0, 0, forced_kind="mention").merge()
        self.assertEqual((merged.user_id, merged.message_id), (current.user_id, "new"))
        self.assertTrue(merged.metadata["has_at_bot"])
        self.assertEqual(merged.source_message_ids, ["old", "new"])

    def test_tool_results_are_atomic_without_losing_interleaved_chat(self) -> None:
        call = {"role": "assistant", "tool_calls": [{"id": "reply"}], "_planner_internal": True}
        visible = {"role": "assistant", "content": "已发出的回答", "_self_message": True}
        output = {"role": "tool", "tool_call_id": "reply", "content": "发送成功"}
        self.assertEqual(protocol_history([call, visible, output]), [call, output, visible])
        self.assertEqual(protocol_history([call, visible]), [visible])

    def test_day_boundary_is_visible_without_changing_original_times(self) -> None:
        entries = [
            {
                "role": "user",
                "content": "昨天的话",
                **message_metadata(timestamp=datetime(2026, 11, 2, tzinfo=timezone.utc)),
            },
            {
                "role": "user",
                "content": "今天的话",
                **message_metadata(timestamp=datetime(2026, 11, 3, tzinfo=timezone.utc)),
            },
        ]
        messages = assemble_messages(request(), entries, "", "", "当前")
        self.assertIn("时间：2026-11-03 08:00:00", str(messages))

    def test_restore_notice_matches_upstream_offline_intervals(self) -> None:
        from src.plugins.nonebot_plugin_fxbot.chat import message_context

        for seconds, expected in [
            (60, "短暂重启"),
            (600, "短暂离线"),
            (3600, "离线休息"),
            (25000, "沉睡了"),
            (90000, "像沉睡了一段时间"),
        ]:
            with (
                self.subTest(seconds=seconds),
                patch.object(message_context, "now", return_value=datetime(2026, 11, 3, tzinfo=timezone.utc)),
            ):
                history = [
                    {
                        "_timestamp": (
                            datetime(2026, 11, 3, tzinfo=timezone.utc) - timedelta(seconds=seconds)
                        ).isoformat()
                    }
                ]
                notice = restore_reference(history)
                self.assertIn(expected, notice["content"])
                self.assertIn("不代表当前用户刚刚发来新消息", notice["content"])


class ContextLifecycleTests(unittest.IsolatedAsyncioTestCase):
    """验证水合、后续消息接入和真实数据库写入链。"""

    async def asyncSetUp(self) -> None:
        await base_models.init_database()
        await migrate.ensure_schema()
        self.tenant = f"message-context-{uuid4().hex}"
        self.addAsyncCleanup(chat_repository.clear_tenant, self.tenant)

    async def test_hydrate_restores_half_window_and_notice_only_once(self) -> None:
        service = ChatService(ChatSessionStore())
        history = [
            {
                "role": "user",
                "content": "旧问题",
                "_source_ids": ["old"],
                **message_metadata(timestamp=datetime(2026, 11, 2, tzinfo=timezone.utc)),
            }
        ]
        with (
            patch.object(chat_repository, "get_recent_messages", AsyncMock(return_value=history)) as load,
            patch("src.plugins.nonebot_plugin_fxbot.chat.memory.state.is_ready", return_value=True),
        ):
            await hydrate(service, self.tenant, 100)
            await hydrate(service, self.tenant, 100)
        load.assert_awaited_once_with(self.tenant, 50)
        entries = service.session_store.get(self.tenant)
        self.assertEqual(sum(bool(item.get("_reference")) for item in entries), 1)
        self.assertEqual(entries[0]["_timestamp"], history[0]["_timestamp"])

    async def test_incoming_message_updates_anchor_without_replacing_logical_turn(self) -> None:
        service = ChatService(ChatSessionStore())
        old, new = request("old"), request("new", metadata={"history_recorded": True, "has_at_bot": True})
        old.text = "旧问题"
        service._active_requests[old.session_id] = old
        service.session_store.append(
            old.session_id, {"role": "user", "content": "答非所问 你在说什么", "_source_ids": ["new"]}
        )
        new_runtime = SimpleNamespace(bot=None, event="new-author-event")
        self.assertTrue(await service.queue_message(new, new_runtime))
        context = SimpleNamespace(
            request=old, entry=None, limit=100, persona_text="", memory_block="", current_content="旧问题"
        )
        turn_id = old.turn_id
        with patch(
            "src.plugins.nonebot_plugin_fxbot.chat.service._memory_block", AsyncMock(return_value="新发言人的参考")
        ):
            messages = await plan_flow.rebuild_messages(service, context)
        self.assertEqual(old.turn_id, turn_id)
        self.assertEqual(old.message_id, "new")
        self.assertTrue(old.metadata["has_at_bot"])
        self.assertIs(context.runtime, new_runtime)
        self.assertIs(old.metadata["planner_runtime"], new_runtime)
        self.assertEqual(context.memory_block, "新发言人的参考")
        self.assertIn("答非所问", str(messages))

    async def test_recall_keeps_memory_and_database_generation_in_sync(self) -> None:
        store = ChatSessionStore()
        identity = "recalled"
        await chat_repository.append_message(
            ChatMessage(tenant=self.tenant, content="撤回原文", dedupe_key="msg:recalled", platform_message_id=identity)
        )
        store.append(self.tenant, {"role": "user", "content": "撤回原文", "_source_ids": [identity]})
        with (
            patch.object(lifecycle, "default_session_store", store),
            patch.object(lifecycle, "is_ready", return_value=True),
        ):
            await lifecycle.handle_recall(self.tenant, identity)
        state = await chat_repository.get_session_state(self.tenant)
        self.assertEqual(store.epoch(self.tenant), state.generation)
        turn_id = await chat_repository.upsert_turn(
            ChatTurn(tenant=self.tenant, turn_key="after-recall", generation=store.epoch(self.tenant))
        )
        self.assertIsNotNone(turn_id)
        item_id = await chat_repository.append_turn_item(
            ChatTurnItem(tenant=self.tenant, turn_id=turn_id, item_seq=1, role="assistant", content="新的回答"),
            generation=store.epoch(self.tenant),
        )
        self.assertTrue(
            await chat_repository.mark_turn_item_sent(self.tenant, item_id, generation=store.epoch(self.tenant))
        )
        history = await chat_repository.get_recent_messages(self.tenant, 10)
        self.assertEqual(history[-1]["content"], "新的回答")

    async def test_sent_reply_identity_and_reference_survive_restart(self) -> None:
        payload = {
            "context": {
                **message_metadata(
                    timestamp=datetime(2026, 11, 3, 2, 35, tzinfo=timezone.utc), sender_name="小鱼", quote_id="question"
                ),
                "_self_message": True,
            }
        }
        turn_id = await chat_repository.upsert_turn(ChatTurn(tenant=self.tenant, turn_key="reply", status="generated"))
        item_id = await chat_repository.append_turn_item(
            ChatTurnItem(
                tenant=self.tenant,
                turn_id=turn_id,
                item_seq=1,
                role="assistant",
                content="我是回答",
                payload=json.dumps(payload),
            )
        )
        await chat_repository.mark_turn_item_sent(self.tenant, item_id, "sent")
        history = await chat_repository.get_recent_messages(self.tenant, 10)
        self.assertEqual(history[-1]["_quote_ids"], ["question"])
        self.assertEqual(history[-1]["_source_ids"], ["sent"])
        self.assertIn('is_self_message="true"', str(assemble_messages(request(), history, "", "", "当前问题")))
