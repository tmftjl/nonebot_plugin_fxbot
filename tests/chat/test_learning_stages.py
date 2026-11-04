"""MaiBot 学习参考的阶段边界、回复意图和 Planner 重建回归。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.plugins.nonebot_plugin_fxbot.chat import service as service_module
from src.plugins.nonebot_plugin_fxbot.chat.types import ChatRequest
from src.plugins.nonebot_plugin_fxbot.chat.heart_flow import conversation
from src.plugins.nonebot_plugin_fxbot.chat.memory.inject import _jargon_hits, context_user_texts


class LearningStageTests(unittest.IsolatedAsyncioTestCase):
    """表达选择只能在回复阶段发生，且使用 Planner 给出的本次回复意图。"""

    def request(self) -> ChatRequest:
        return ChatRequest("tenant", "user", "group", "原消息", "turn", "m1", "mention")

    async def test_reply_reference_and_intent_are_used_without_short_query(self) -> None:
        request = self.request()
        request.metadata["expression_intent"] = {"focus": "安慰朋友", "tone": "温柔"}
        history = [{"role": "user", "_sender_id": "user", "content": "之前的聊天"}]
        reference = "先安慰，再给建议" * 100
        with patch.object(service_module, "learning_context", AsyncMock(return_value="句式")) as learning:
            self.assertEqual(
                await service_module._learning_reference(request, history, reply_reference=reference), "句式"
            )
        self.assertIn(reference, learning.await_args.args[1])
        self.assertIn("安慰朋友", learning.await_args.args[1])
        self.assertEqual(learning.await_args.kwargs["phase"], "reply")
        self.assertIs(learning.await_args.kwargs["history"], history)

    async def test_planner_rebuild_reuses_reference_identity(self) -> None:
        request = self.request()
        with patch.object(service_module, "learning_context", AsyncMock(return_value="黑话参考")) as learning:
            await service_module._learning_reference(request, [])
            references = learning.await_args.kwargs["references"]
            references["词"] = "词：释义"
            await service_module._learning_reference(request, [])
        self.assertEqual(learning.await_args.kwargs["phase"], "planner")
        self.assertIs(learning.await_args.kwargs["references"], references)

    def test_expression_intent_schema_tracks_vector_hot_reload(self) -> None:
        for enabled in (False, True, False):
            with patch.object(conversation, "expression_vector_config", return_value={"enabled": enabled}):
                reply = next(tool for tool in conversation.control_tools(False) if tool["function"]["name"] == "reply")
            self.assertEqual("expression_intent" in reply["function"]["parameters"]["properties"], enabled)

    def test_jargon_ranking_and_duplicate_scope_match_upstream(self) -> None:
        rows = [
            SimpleNamespace(normalized="晚词", term="晚词", meaning="释义", count=4),
            SimpleNamespace(normalized="早词", term="早词", meaning="释义", count=4),
            SimpleNamespace(normalized="高频", term="高频", meaning="释义", count=9),
            SimpleNamespace(normalized="早词", term="早词", meaning="共享重复", count=2),
        ]
        hits = _jargon_hits(rows, ["早词", "晚词 高频"], 10)
        self.assertEqual([row.term for row in hits], ["高频", "早词", "晚词"])

    def test_user_context_filters_self_internal_and_duplicate_ids(self) -> None:
        base = {"role": "user", "_sender_id": "user", "_source_ids": ["m1"], "content": "真实消息"}
        messages = [base, base, {**base, "_self_message": True}, {**base, "_planner_internal": True}]
        self.assertEqual(context_user_texts(messages), ["真实消息"])
