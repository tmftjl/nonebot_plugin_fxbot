"""一轮对话追踪的真实持久化、阶段顺序和多次交付。"""

from __future__ import annotations

import json
import unittest
from copy import deepcopy
from uuid import uuid4
from types import SimpleNamespace
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

from src.plugins.nonebot_plugin_fxbot.db import base_models
from src.plugins.nonebot_plugin_fxbot.chat import service as service_module
from src.plugins.nonebot_plugin_fxbot.chat.tools import ToolRuntime
from src.plugins.nonebot_plugin_fxbot.chat.types import ChatRequest
from src.plugins.nonebot_plugin_fxbot.chat.memory import state as memory_state, migrate
from src.plugins.nonebot_plugin_fxbot.chat.session import ChatSessionStore
from src.plugins.nonebot_plugin_fxbot.chat.providers import LLMResponse, routing, provider_manager
from src.plugins.nonebot_plugin_fxbot.chat.heart_flow import conversation
from src.plugins.nonebot_plugin_fxbot.chat.memory.repository import chat_repository
from src.plugins.nonebot_plugin_fxbot.config.system_defaults import SYSTEM_DEFAULTS


def call(name: str, **arguments) -> dict:
    """构造一个有效模型工具调用。"""
    return {"id": uuid4().hex, "function": {"name": name, "arguments": json.dumps(arguments)}}


class TurnTraceTests(unittest.IsolatedAsyncioTestCase):
    """真实服务和 SQLite 验证逻辑轮次仅保存一条追踪。"""

    async def asyncSetUp(self) -> None:
        await base_models.init_database()
        await migrate.ensure_schema()
        memory_state.mark_ready()
        self.tenant = "test:turn-trace:" + uuid4().hex
        self.addAsyncCleanup(chat_repository.clear_tenant, self.tenant)

    async def run_conversation(self, *, count: int = 1, failed: bool = False, focus: bool = False):
        config = deepcopy(SYSTEM_DEFAULTS["chat"])
        config["provider"] = "test"
        config["providers"] = {"test": {"provider_type": "chat", "model": "test"}}
        config["response_splitter"]["enable"] = False
        manager = SimpleNamespace(get_system=lambda: {"chat": config})
        provider = SimpleNamespace(
            provider_id="test", config={"model": "test"}, get_model=lambda: "test", chat=AsyncMock()
        )
        replies = [LLMResponse(content=f"测试回复{index}") for index in range(count)]
        provider.chat.side_effect = (
            replies
            if focus
            else [
                LLMResponse(tool_calls=[call("reply", msg_id="source", reply_reference="接话") for _ in range(count)]),
                *replies,
                LLMResponse(tool_calls=[call("stop")]),
            ]
        )

        @asynccontextmanager
        async def lease(*args):
            yield provider

        service = service_module.ChatService(ChatSessionStore())
        matcher = SimpleNamespace(send=AsyncMock(return_value={"message_id": "receipt"}))
        if failed:
            matcher.send.side_effect = [{"message_id": "receipt"}, RuntimeError("synthetic send failure")]
        request = ChatRequest(
            self.tenant, "user", None, "你好", uuid4().hex, uuid4().hex, "private", message_id="source"
        )
        with (
            patch.object(service_module, "get_config_manager", return_value=manager),
            patch.object(routing, "get_config_manager", return_value=manager),
            patch.object(provider_manager, "lease", lease),
            patch.object(service_module, "_memory_block", AsyncMock(return_value="")),
            patch.object(service_module.generator, "allowed_tools", AsyncMock(return_value=(set(), []))),
            patch.object(conversation, "allowed_tools", AsyncMock(return_value=(set(), []))),
            patch.object(conversation, "planner_config", return_value={"passive_replyer": False}),
            patch.object(conversation.plan_flow, "steering_enabled", return_value=False),
            patch.object(
                service_module.sender_module,
                "apply_typo",
                side_effect=lambda text: SimpleNamespace(text=text, correction=""),
            ),
            patch.object(service_module.sender_module, "send_voice", AsyncMock()),
            patch.object(service_module.sender_module, "send_emoji", AsyncMock()),
            patch.object(service_module.sender_module.chat_writeback, "on_reply"),
        ):
            runtime = ToolRuntime(matcher=matcher)
            if focus:
                await service.focus_reply(request, runtime, "接话", is_valid=lambda: True)
            else:
                await service.process_and_send(request, runtime)
        return await chat_repository.list_traces(self.tenant), matcher

    async def test_planner_replyer_and_send_are_one_record(self) -> None:
        rows, matcher = await self.run_conversation()
        self.assertEqual(len(rows), 1)
        self.assertEqual(matcher.send.await_count, 1)
        budget = json.loads(rows[0].budget)
        self.assertEqual([stage["kind"] for stage in budget["stages"]], ["planner", "replyer", "send", "planner"])
        self.assertEqual(budget["send"]["sent_parts"], 1)
        self.assertEqual(rows[0].request_preview, "你好")
        self.assertAlmostEqual(rows[0].latency_ms, budget["timings"]["total_ms"], delta=5)

    async def test_multiple_replies_keep_each_stage_and_aggregate_delivery(self) -> None:
        rows, matcher = await self.run_conversation(count=2)
        self.assertEqual(len(rows), 1)
        self.assertEqual(matcher.send.await_count, 2)
        budget = json.loads(rows[0].budget)
        self.assertEqual(sum(stage["kind"] == "replyer" for stage in budget["stages"]), 2)
        self.assertEqual(sum(stage["kind"] == "send" for stage in budget["stages"]), 2)
        self.assertEqual(budget["send"]["sent_parts"], 2)
        self.assertEqual(budget["send"]["total_parts"], 2)

    async def test_partial_delivery_failure_is_visible_in_same_record(self) -> None:
        rows, _ = await self.run_conversation(count=2, failed=True)
        self.assertEqual(len(rows), 1)
        budget = json.loads(rows[0].budget)
        self.assertEqual(budget["send"]["sent_parts"], 1)
        self.assertEqual(budget["send"]["total_parts"], 2)
        self.assertEqual(budget["send"]["failed_index"], 1)
        self.assertIn("synthetic send failure", rows[0].error)

    async def test_focus_reply_appends_delivery_to_existing_record(self) -> None:
        rows, _ = await self.run_conversation(focus=True)
        self.assertEqual(len(rows), 1)
        budget = json.loads(rows[0].budget)
        self.assertEqual([stage["kind"] for stage in budget["stages"]], ["replyer", "send"])
        self.assertEqual(budget["send"]["sent_parts"], 1)
        self.assertEqual(rows[0].latency_ms, budget["timings"]["total_ms"])

    async def test_wait_updates_same_record_without_truncating_stage_json(self) -> None:
        arguments = dict(
            turn_id="same-turn", trigger_kind="private", request_preview="你好", injected="{}", provider="test"
        )
        await chat_repository.save_trace(self.tenant, **arguments, budget="{}", latency_ms=1)
        stages = [{"kind": "planner", "label": "阶段" * 100} for _ in range(10)]
        await chat_repository.save_trace(self.tenant, **arguments, budget=json.dumps({"stages": stages}), latency_ms=2)
        rows = await chat_repository.list_traces(self.tenant)
        self.assertEqual(len(rows), 1)
        self.assertEqual(json.loads(rows[0].budget)["stages"], stages)
        self.assertEqual(rows[0].latency_ms, 2)
