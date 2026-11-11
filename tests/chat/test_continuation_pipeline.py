"""续话的真实提示组装、学习参考、SQLite 轮次记录与发送可见性回归。"""

from __future__ import annotations

import json
import unittest
from copy import deepcopy
from uuid import uuid4
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.plugins.nonebot_plugin_fxbot.db import base_models
from src.plugins.nonebot_plugin_fxbot.chat import hooks, service as service_module
from src.plugins.nonebot_plugin_fxbot.config import SYSTEM_DEFAULTS
from src.plugins.nonebot_plugin_fxbot.chat.tools import ToolRuntime
from src.plugins.nonebot_plugin_fxbot.chat.types import ChatRequest
from src.plugins.nonebot_plugin_fxbot.chat.memory import migrate
from src.plugins.nonebot_plugin_fxbot.chat.replyer import prompt
from src.plugins.nonebot_plugin_fxbot.chat.session import ChatSessionStore
from src.plugins.nonebot_plugin_fxbot.chat.providers import LLMResponse
from src.plugins.nonebot_plugin_fxbot.chat.memory.models import ChatTurn, ChatMessage, ChatTurnItem
from src.plugins.nonebot_plugin_fxbot.chat.message_receive import history as history_module


class ContinuationPipelineTests(unittest.IsolatedAsyncioTestCase):
    """不 mock assemble_messages 或持久化入口，直接核对新轮次的真实数据库记录。"""

    async def asyncSetUp(self) -> None:
        await base_models.init_database()
        await migrate.ensure_schema()
        self.tenant = f"test:continuation:private:{uuid4().hex}"
        self.service = service_module.ChatService(ChatSessionStore())
        self.history = [
            {
                "role": "user",
                "content": "小明(user): 我今天去了公园。",
                "_sender_id": "user",
                "_source_ids": ["real-message"],
            },
            {
                "role": "assistant",
                "content": "公园里有什么有趣的？",
                "_self_message": True,
                "_source_ids": ["real-reply"],
            },
        ]
        self.service.session_store.append_many(self.tenant, deepcopy(self.history), limit=20)
        self.request = ChatRequest(
            self.tenant,
            "",
            None,
            "",
            f"extension-{uuid4().hex}",
            f"extension:{uuid4().hex}",
            "proactive",
            proactive=True,
            platform="test",
            accepted_epoch=0,
            metadata={"continuation": True, "_extension_owner": "test", "planner_reply": True},
        )
        config = deepcopy(SYSTEM_DEFAULTS)
        config["chat"].update(persona="test", max_private_context_size=20)
        self.enterContext(
            patch.object(service_module, "get_config_manager", return_value=SimpleNamespace(get_system=lambda: config))
        )
        self.enterContext(patch.object(service_module.memory_state, "is_ready", return_value=True))
        self.enterContext(patch.object(history_module, "history_enabled", return_value=True))
        self.enterContext(patch.object(hooks, "_hooks", {}))
        self.enterContext(patch.object(service_module, "get_bot_group_role", AsyncMock(return_value=None)))
        self.enterContext(patch.object(service_module, "_memory_block", AsyncMock(return_value="")))
        self.learning = self.enterContext(
            patch.object(service_module, "_learning_reference", AsyncMock(return_value=""))
        )
        self.enterContext(patch.object(prompt, "build_persona_text", return_value=("test", "正常人格")))
        self.tools = self.enterContext(
            patch.object(service_module.generator, "allowed_tools", AsyncMock(return_value=(set(), [])))
        )
        self.model = SimpleNamespace(
            chat=AsyncMock(return_value=LLMResponse(content="还想听听你在公园的见闻。")), provider_id="fake"
        )

        async def shape(response, **kwargs):
            return response, [SimpleNamespace(text=response.content)], response.content

        self.enterContext(patch.object(service_module.generator, "shape_reply", shape))

    async def test_continuation_has_no_synthetic_user_in_prompt_learning_or_database(self) -> None:
        instruction = "顺着公园的话题自然补充一句"
        runtime = ToolRuntime(allowed_tools=frozenset())
        response = await self.service._process_with_provider(
            self.request, runtime, self.model, focus_instruction=instruction
        )
        messages = self.model.chat.await_args.kwargs["messages"]
        serialized = json.dumps(messages, ensure_ascii=False)
        self.assertNotIn("[图片]", serialized)
        self.assertNotIn(self.request.dedupe_key, serialized)
        self.assertNotIn("[无可见文本内容]", serialized)
        self.assertIn("没有新的用户消息", serialized)
        self.assertIn(instruction, serialized)
        self.assertIn("我今天去了公园", serialized)
        self.assertEqual(self.learning.await_args.args[1], self.history)
        self.assertEqual(self.service.session_store.get(self.tenant), self.history)
        self.assertEqual(self.tools.await_args.kwargs["proactive"], True)
        self.assertIsNone(self.model.chat.await_args.kwargs["tools"])

        turns = await ChatTurn.select_rows(tenant=self.tenant)
        self.assertEqual(len(turns), 1)
        self.assertEqual(json.loads(turns[0].author_ids), [])
        self.assertEqual(json.loads(turns[0].trigger_message_ids), [])
        self.assertEqual(await ChatMessage.select_rows(tenant=self.tenant), [])
        items = await ChatTurnItem.select_rows(tenant=self.tenant)
        self.assertEqual(
            [(item.role, item.content, item.visible) for item in items], [("assistant", response.text, False)]
        )

        await self.service.mark_sent(self.request, response, 0, "new-visible-reply", content=response.text)
        items = await ChatTurnItem.select_rows(tenant=self.tenant)
        self.assertTrue(items[0].visible)
        self.assertEqual(self.service.session_store.get(self.tenant)[-1]["content"], response.text)
        self.assertEqual(await ChatMessage.select_rows(tenant=self.tenant), [])

    async def test_persistence_does_not_invent_user_source_even_if_content_argument_is_nonempty(self) -> None:
        with patch.object(history_module, "_prepare_image_memory", AsyncMock()) as prepare:
            persisted = await self.service._persist_turn_start(self.request, "系统续话任务", 0)
        self.assertIsNotNone(persisted)
        self.assertIsNone(persisted[1])
        prepare.assert_not_called()
        self.assertEqual(await ChatMessage.select_rows(tenant=self.tenant), [])
        self.assertEqual(await ChatTurnItem.select_rows(tenant=self.tenant), [])

    async def test_continuation_cannot_fall_into_normal_planner_without_instruction(self) -> None:
        with self.assertRaises(ValueError):
            await self.service._process_with_provider(self.request, ToolRuntime(), self.model)
        self.model.chat.assert_not_called()

    def test_build_user_content_and_assembly_skip_current_input_for_continuation(self) -> None:
        self.assertEqual(prompt.build_user_content(self.request), "")
        messages = prompt.assemble_messages(self.request, [], "人格", "", "不得变成用户输入")
        self.assertEqual(messages, [{"role": "system", "content": "人格"}])
