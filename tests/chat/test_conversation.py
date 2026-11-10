"""普通聊天的 Planner 工具循环与会话隔离回归。"""

from __future__ import annotations

import json
import base64
import asyncio
import unittest
from types import SimpleNamespace
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

from src.plugins.nonebot_plugin_fxbot.chat.types import ChatRequest, ChatResponse
from src.plugins.nonebot_plugin_fxbot.chat.session import ChatSessionStore
from src.plugins.nonebot_plugin_fxbot.chat.providers import LLMResponse
from src.plugins.nonebot_plugin_fxbot.chat.heart_flow import conversation


def call(name: str, **arguments: object) -> dict:
    """构造模型工具调用。"""
    return {"id": name, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}


class ConversationTests(unittest.IsolatedAsyncioTestCase):
    """验证 Planner 控制流，不发起外部模型请求。"""

    async def asyncSetUp(self) -> None:
        self.request = ChatRequest("tenant-a", "user", "group", "你好", "turn", "dedupe", "mention", message_id="m1")
        self.store = ChatSessionStore()
        self.service = SimpleNamespace(
            session_store=self.store,
            is_current=lambda tenant, epoch: self.store.epoch(tenant) == epoch,
            _process_locked=AsyncMock(return_value=ChatResponse(text="可见回复")),
            _send_generated=AsyncMock(return_value=True),
        )
        self.context = SimpleNamespace(
            request=self.request,
            epoch=0,
            messages=[{"role": "user", "content": "你好"}],
            runtime=SimpleNamespace(matcher=object()),
            provider_name="test",
            limit=30,
            entry=None,
            persona_text="",
            memory_block="",
            current_content="你好",
        )
        self.store.append("tenant-a", {"role": "user", "content": "你好", "_source_ids": ["m1"]})
        self.provider = SimpleNamespace(chat=AsyncMock())
        self.enterContext(patch.object(conversation, "end_turn_without_reply", AsyncMock()))
        self.enterContext(patch.object(conversation, "record_plan_sample", AsyncMock()))

        @asynccontextmanager
        async def provider_scope(*args):
            yield self.provider

        self.enterContext(patch.object(conversation, "fallback_provider", provider_scope))
        self.enterContext(patch.object(conversation, "allowed_tools", AsyncMock(return_value=({"lookup"}, []))))
        self.enterContext(patch.object(conversation, "planner_config", return_value={"passive_replyer": False}))
        self.enterContext(patch.object(conversation.plan_flow, "steering_enabled", return_value=False))
        self.execute = self.enterContext(
            patch.object(conversation, "execute_tool", AsyncMock(return_value={"ok": True}))
        )
        self.enterContext(
            patch.object(
                conversation,
                "prepare_tool_result_content",
                AsyncMock(side_effect=lambda result, **kw: json.dumps(result)),
            )
        )

    async def test_tool_result_returns_to_planner_before_reply(self) -> None:
        self.provider.chat.side_effect = [
            LLMResponse(tool_calls=[call("lookup", query="天气")]),
            LLMResponse(tool_calls=[call("reply", msg_id="m1", reply_reference="说明查到的天气")]),
            LLMResponse(tool_calls=[call("stop")]),
        ]
        response = await conversation.run(self.service, self.context)
        self.assertTrue(response.metadata["planner_sent"])
        self.assertEqual(self.provider.chat.await_count, 3)
        self.assertEqual(self.execute.await_args.args[2].session_id, "tenant-a")
        self.service._send_generated.assert_awaited_once()
        self.assertEqual(self.service._process_locked.await_args.kwargs["focus_instruction"], "说明查到的天气")

    async def test_plain_planner_text_does_not_trigger_reply(self) -> None:
        self.provider.chat.return_value = LLMResponse(content="暂时没有必要发言")
        response = await conversation.run(self.service, self.context)
        self.assertFalse(response.metadata["planner_sent"])
        self.service._process_locked.assert_not_awaited()

    async def test_repeated_target_without_new_information_stops(self) -> None:
        self.provider.chat.side_effect = [
            LLMResponse(tool_calls=[call("reply", msg_id="m1", reply_reference="回答")]),
            LLMResponse(tool_calls=[call("reply", msg_id="m1", reply_reference="换种说法再回答")]),
        ]
        await conversation.run(self.service, self.context)
        self.service._send_generated.assert_awaited_once()
        self.assertEqual(self.provider.chat.await_count, 2)

    async def test_new_tool_result_allows_supplement_but_same_result_does_not(self) -> None:
        self.provider.chat.side_effect = [
            LLMResponse(tool_calls=[call("reply", msg_id="m1", reply_reference="先说明进度")]),
            LLMResponse(tool_calls=[call("lookup", query="内存")]),
            LLMResponse(tool_calls=[call("reply", msg_id="m1", reply_reference="汇报新结果")]),
            LLMResponse(tool_calls=[call("lookup", query="内存")]),
            LLMResponse(tool_calls=[call("reply", msg_id="m1", reply_reference="重复结果")]),
        ]
        await conversation.run(self.service, self.context)
        self.assertEqual(self.service._send_generated.await_count, 2)
        self.assertEqual(self.execute.await_count, 2)

    async def test_different_targets_still_allow_multiple_replies(self) -> None:
        self.enterContext(
            patch.object(conversation.chat_repository, "message_by_identity", AsyncMock(return_value=None))
        )
        self.store.append("tenant-a", {"role": "user", "content": "另一条", "_source_ids": ["m2"]})
        self.provider.chat.side_effect = [
            LLMResponse(
                tool_calls=[
                    call("reply", msg_id="m1", reply_reference="回复第一条"),
                    call("reply", msg_id="m2", reply_reference="回复第二条"),
                ]
            ),
            LLMResponse(tool_calls=[call("stop")]),
        ]
        await conversation.run(self.service, self.context)
        self.assertEqual(self.service._send_generated.await_count, 2)
        children = [call.args[0] for call in self.service._process_locked.await_args_list]
        self.assertEqual({child.turn_id for child in children}, {self.request.turn_id})
        self.assertEqual([child.metadata["turn_attempt"] for child in children], [1, 2])

    async def test_new_incoming_message_during_reply_allows_supplement(self) -> None:
        async def send(*args):
            self.store.mark_seen("tenant-a", "new-message")
            return True

        self.service._send_generated.side_effect = send
        self.provider.chat.side_effect = [
            LLMResponse(tool_calls=[call("reply", msg_id="m1", reply_reference="先回答")]),
            LLMResponse(tool_calls=[call("reply", msg_id="m1", reply_reference="针对新信息补充")]),
            LLMResponse(tool_calls=[call("stop")]),
        ]
        await conversation.run(self.service, self.context)
        self.assertEqual(self.service._send_generated.await_count, 2)

    async def test_repeated_target_gets_upstream_supplement_reminder(self) -> None:
        self.store.append(
            "tenant-a", {"role": "assistant", "content": "之前的回答", "_self_message": True, "_quote_ids": ["m1"]}
        )
        self.provider.chat.side_effect = [
            LLMResponse(tool_calls=[call("reply", msg_id="m1", reply_reference="补充说明")]),
            LLMResponse(tool_calls=[call("stop")]),
        ]
        await conversation.run(self.service, self.context)
        guidance = self.service._process_locked.await_args.kwargs["focus_instruction"]
        self.assertIn("你刚刚已经回复过这条消息", guidance)
        self.assertIn("之前的回答", guidance)

    async def test_reply_target_keeps_its_own_sender_role(self) -> None:
        self.request.metadata["sender_role"] = "owner"
        self.store.get("tenant-a")[0]["_sender_role"] = "member"
        self.provider.chat.side_effect = [
            LLMResponse(tool_calls=[call("reply", msg_id="m1", reply_reference="回答这条消息")]),
            LLMResponse(tool_calls=[call("stop")]),
        ]
        await conversation.run(self.service, self.context)
        self.assertEqual(self.service._process_locked.await_args.args[0].metadata["sender_role"], "member")

    async def test_reply_to_a_quoted_message_does_not_target_its_quote_author(self) -> None:
        snapshot = {"message_id": "quoted", "sender_id": "other", "sender_name": "张三", "text": "原文"}
        self.store.get("tenant-a")[0].update({"_quote_snapshot": snapshot, "_quote_ids": ["quoted"]})
        self.provider.chat.side_effect = [
            LLMResponse(tool_calls=[call("reply", msg_id="m1", reply_reference="回答当前用户的引用问题")]),
            LLMResponse(tool_calls=[call("stop")]),
        ]
        await conversation.run(self.service, self.context)
        child = self.service._process_locked.await_args.args[0]
        self.assertEqual((child.user_id, child.message_id, child.reply_to_message_id), ("user", "m1", "m1"))
        self.assertEqual(child.turn_id, self.request.turn_id)
        self.assertEqual(child.metadata["turn_attempt"], 1)
        self.assertEqual(child.metadata["target_quote_ids"], ["quoted"])
        self.assertEqual(child.metadata["quote_snapshot"], snapshot)

    async def test_selected_history_does_not_inherit_trigger_message_quote(self) -> None:
        self.request.metadata["quote_snapshot"] = {"message_id": "wrong", "sender_name": "不是目标引用的人"}
        snapshot = {"message_id": "quoted", "sender_name": "原作者", "text": "目标引用原文"}
        self.store.append(
            "tenant-a",
            {
                "role": "user",
                "content": "旧用户的问题",
                "_source_ids": ["old"],
                "_sender_id": "old-user",
                "_quote_ids": ["quoted"],
                "_quote_snapshot": snapshot,
            },
        )
        self.provider.chat.side_effect = [
            LLMResponse(tool_calls=[call("reply", msg_id="old", reply_reference="回答旧问题")]),
            LLMResponse(tool_calls=[call("stop")]),
        ]
        with patch.object(conversation.chat_repository, "message_by_identity", AsyncMock(return_value=None)):
            await conversation.run(self.service, self.context)
        child = self.service._process_locked.await_args.args[0]
        self.assertEqual(child.metadata["quote_snapshot"], snapshot)
        self.assertEqual(child.user_id, "old-user")

    async def test_passive_reply_target_is_frozen_before_model_request(self) -> None:
        async def generate(**kwargs):
            self.store.append("tenant-a", {"role": "user", "content": "后来到的消息", "_source_ids": ["later"]})
            return LLMResponse(content="回应原来那条消息")

        self.provider.chat.side_effect = generate
        with patch.object(conversation, "planner_config", return_value={"passive_replyer": True}):
            await conversation.run(self.service, self.context)
        self.assertEqual(self.service._process_locked.await_args.args[0].message_id, "m1")

    async def test_planner_protocol_is_saved_before_later_im_messages(self) -> None:
        self.provider.chat.side_effect = [
            LLMResponse(tool_calls=[call("reply", msg_id="m1", reply_reference="回答")]),
            LLMResponse(tool_calls=[call("stop")]),
        ]

        async def send(*args):
            self.store.append("tenant-a", {"role": "assistant", "content": "实际发言", "_self_message": True})
            return True

        self.service._send_generated.side_effect = send
        await conversation.run(self.service, self.context)
        history = self.store.get("tenant-a")
        call_index = next(index for index, item in enumerate(history) if item.get("tool_calls"))
        reply_index = next(index for index, item in enumerate(history) if item.get("_self_message"))
        self.assertLess(call_index, reply_index)

    async def test_long_conversation_compacts_before_next_internal_request(self) -> None:
        for index in range(40):
            self.store.append("tenant-a", {"role": "user", "content": f"历史{index}"})
        calls = 0

        async def generate(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                return LLMResponse(tool_calls=[call("lookup", query="资料")])
            visible = [item for item in self.store.get("tenant-a") if not item.get("_planner_internal")]
            self.assertLessEqual(len(visible), 6)
            return LLMResponse(tool_calls=[call("stop")])

        self.provider.chat.side_effect = generate
        with patch("src.plugins.nonebot_plugin_fxbot.chat.context_summary.compact_messages", AsyncMock()) as compact:
            await conversation.run(self.service, self.context)
        compact.assert_awaited_once()

    async def test_tool_picture_can_be_attached_in_the_same_model_response(self) -> None:
        self.execute.return_value = {
            "ok": True,
            "result": {
                "content_items": [{"content_type": "image", "data": base64.b64encode(b"image").decode("ascii")}]
            },
        }
        self.provider.chat.side_effect = [
            LLMResponse(
                tool_calls=[
                    call("lookup", query="图片"),
                    call(
                        "reply",
                        msg_id="m1",
                        reply_reference="展示工具返回的图",
                        attach_pic=[{"media_index": "tool_result:lookup:1"}],
                    ),
                ]
            ),
            LLMResponse(tool_calls=[call("stop")]),
        ]
        await conversation.run(self.service, self.context)
        self.service._send_generated.assert_awaited_once()
        response = self.service._send_generated.await_args.args[1]
        self.assertEqual(response.metadata["attach_pictures"], [b"image"])

    async def test_passive_text_becomes_guidance(self) -> None:
        self.provider.chat.return_value = LLMResponse(content="顺着话题接一句")
        with patch.object(conversation, "planner_config", return_value={"passive_replyer": True}):
            await conversation.run(self.service, self.context)
        self.assertEqual(self.service._process_locked.await_args.kwargs["focus_instruction"], "顺着话题接一句")
        names = [tool["function"]["name"] for tool in self.provider.chat.await_args.kwargs["tools"]]
        self.assertNotIn("reply", names)

    async def test_wait_preserves_tool_results_and_accepts_short_duration(self) -> None:
        self.provider.chat.return_value = LLMResponse(tool_calls=[call("wait", seconds=2)])
        response = await conversation.run(self.service, self.context)
        self.assertEqual(response.metadata["wait_seconds"], 2)
        self.assertEqual(self.request.metadata["planner_trace"][-1]["role"], "tool")
        self.service._process_locked.assert_not_awaited()

    async def test_passive_wait_clears_reply_options(self) -> None:
        self.request.metadata["planner_reply_options"] = {"reply_style": "长回复"}
        self.provider.chat.return_value = LLMResponse(tool_calls=[call("wait", seconds=2)])
        with patch.object(conversation, "planner_config", return_value={"passive_replyer": True}):
            response = await conversation.run(self.service, self.context)
        self.assertEqual(response.metadata["wait_seconds"], 2)
        self.assertNotIn("planner_reply_options", self.request.metadata)

    async def test_wait_rejects_missing_negative_and_fractional_seconds(self) -> None:
        for seconds in (None, -1, 1.5, True):
            with self.subTest(seconds=seconds):
                self.provider.chat.side_effect = [
                    LLMResponse(tool_calls=[call("wait", seconds=seconds)]),
                    LLMResponse(tool_calls=[call("stop")]),
                ]
                response = await conversation.run(self.service, self.context)
                self.assertNotIn("wait_seconds", response.metadata)

    async def test_foreign_session_target_is_rejected(self) -> None:
        self.store.append("tenant-b", {"role": "user", "content": "其他会话", "_source_ids": ["foreign"]})
        self.provider.chat.side_effect = [
            LLMResponse(tool_calls=[call("reply", msg_id="foreign", reply_reference="串群回复")]),
            LLMResponse(tool_calls=[call("stop")]),
        ]
        await conversation.run(self.service, self.context)
        self.service._process_locked.assert_not_awaited()

    async def test_stop_prevents_later_tools_in_same_response(self) -> None:
        self.provider.chat.return_value = LLMResponse(tool_calls=[call("stop"), call("lookup", query="x")])
        await conversation.run(self.service, self.context)
        self.execute.assert_not_awaited()

    async def test_new_message_interrupts_stream_and_rebuilds(self) -> None:
        started = asyncio.Event()
        cancelled = asyncio.Event()
        attempts = 0

        async def stream(**kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()
            yield LLMResponse(tool_calls=[call("stop")])

        self.provider.chat_stream = stream
        with (
            patch.object(conversation.plan_flow, "steering_enabled", return_value=True),
            patch.object(conversation.plan_flow, "rebuild_messages", AsyncMock(return_value=[])) as rebuild,
        ):
            task = asyncio.create_task(conversation.run(self.service, self.context))
            await started.wait()
            self.store.mark_seen("tenant-a", "new-message")
            await asyncio.wait_for(task, timeout=2)
        self.assertTrue(cancelled.is_set())
        self.assertEqual(attempts, 2)
        rebuild.assert_awaited_once()
