"""P5-1 规划：动作解析、执行分派、样本落库与控制台追溯。"""

from __future__ import annotations

import json
import asyncio
import unittest
from uuid import uuid4
from types import SimpleNamespace
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from src.plugins.nonebot_plugin_fxbot.db import base_models
from src.plugins.nonebot_plugin_fxbot.chat import service as service_module
from src.plugins.nonebot_plugin_fxbot.chat.types import ChatRequest, ChatResponse
from src.plugins.nonebot_plugin_fxbot.chat.memory import state as memory_state, migrate
from src.plugins.nonebot_plugin_fxbot.chat.service import ChatService
from src.plugins.nonebot_plugin_fxbot.chat.session import ChatSessionStore
from src.plugins.nonebot_plugin_fxbot.chat.providers import LLMResponse, provider_manager
from src.plugins.nonebot_plugin_fxbot.console.routes import chat as chat_routes
from src.plugins.nonebot_plugin_fxbot.chat.memory.models import ChatTurn, ChatPlannerSample
from src.plugins.nonebot_plugin_fxbot.chat.memory.repository import chat_repository


class _FakeConfig:
    """只提供对话与规划需要的配置。"""

    def __init__(self, planner: dict[str, object] | None = None) -> None:
        self.planner = planner or {}

    def get_system(self) -> dict[str, object]:
        return {
            "chat": {
                "max_context_size": 10,
                "persona": "",
                "provider": "",
                "model_routing": {"planner": ""},
                "planner": self.planner,
            }
        }


def _lease(provider: object):
    @asynccontextmanager
    async def _fake_lease(_name: str):
        yield provider

    return _fake_lease


def _request(tenant: str, **values: object) -> ChatRequest:
    """构造最小对话请求。"""
    fields = {
        "session_id": tenant,
        "user_id": "user",
        "group_id": "group",
        "text": "你好",
        "turn_id": uuid4().hex,
        "dedupe_key": f"msg:{uuid4().hex}",
        "trigger_kind": "mention",
        "user_name": "Alice",
    }
    fields.update(values)
    return ChatRequest(**fields)  # type: ignore[arg-type]


class PlannerServiceTests(unittest.IsolatedAsyncioTestCase):
    """真实服务与原生工具 Planner 的接线和持久化。"""

    async def asyncSetUp(self) -> None:
        await base_models.init_database()
        await migrate.ensure_schema()
        memory_state.mark_ready()
        self.tenant = f"planner-{uuid4().hex}"
        self.addAsyncCleanup(chat_repository.clear_tenant, self.tenant)

    async def test_stop_ends_persisted_turn(self) -> None:
        provider = SimpleNamespace(
            provider_id="test",
            get_model=lambda: "test",
            chat=AsyncMock(
                return_value=LLMResponse(
                    tool_calls=[{"id": "stop-1", "type": "function", "function": {"name": "stop", "arguments": "{}"}}]
                )
            ),
        )
        request = _request(self.tenant)
        service = ChatService(ChatSessionStore())
        with (
            patch.object(service_module, "get_config_manager", return_value=_FakeConfig()),
            patch.object(provider_manager, "lease", _lease(provider)),
        ):
            response = await service.process(request)
        self.assertEqual(response.text, "")
        async with base_models.async_maker() as session:
            rows = await ChatTurn.select_rows(tenant=self.tenant, turn_key=request.turn_id, session=session)
        self.assertEqual(rows[0].status, "ended")
        self.assertEqual(provider.chat.await_count, 1)

    async def test_real_service_injects_learning_as_stage_specific_user_message(self) -> None:
        """真实服务按阶段获取学习参考，独立追加 user 消息而非并进长期记忆 system。"""
        for instruction in (None, "安慰对方，别打趣"):
            provider = SimpleNamespace(
                provider_id="test",
                get_model=lambda: "test",
                chat=AsyncMock(return_value=LLMResponse(content="好的")),
            )
            service = ChatService(ChatSessionStore())
            request = _request(self.tenant)
            captured = []

            async def planner(_service, context):
                captured.extend(context.messages)
                return ChatResponse(text="")

            with (
                patch.object(service_module, "get_config_manager", return_value=_FakeConfig()),
                patch.object(service_module, "learning_context", AsyncMock(return_value="独立学习参考")) as learning,
                patch.object(service_module, "_memory_block", AsyncMock(return_value="长期记忆")),
                patch.object(service_module.conversation, "run", planner),
                patch.object(
                    service_module.generator, "generate", AsyncMock(return_value=LLMResponse(content="好的"))
                ) as generate,
            ):
                await service._process_with_provider(request, None, provider, focus_instruction=instruction)
            if instruction is not None:
                captured = generate.await_args.args[4]
            self.assertIn({"role": "user", "content": "独立学习参考"}, captured)
            self.assertIn({"role": "system", "content": "长期记忆"}, captured)
            self.assertEqual(learning.await_args.kwargs["phase"], "planner" if instruction is None else "reply")
            self.assertEqual(learning.await_args.args[1], instruction or "")

    async def test_planner_error_does_not_generate_unsolicited_reply(self) -> None:
        provider = SimpleNamespace(
            provider_id="test",
            get_model=lambda: "test",
            chat=AsyncMock(side_effect=RuntimeError("model unavailable")),
        )
        with (
            patch.object(service_module, "get_config_manager", return_value=_FakeConfig()),
            patch.object(provider_manager, "lease", _lease(provider)),
        ):
            with self.assertRaises(RuntimeError):
                await ChatService(ChatSessionStore()).process(_request(self.tenant))
        self.assertEqual(provider.chat.await_count, 1)

    async def test_wait_releases_turn_lock(self) -> None:
        request = _request(self.tenant)
        service = ChatService(ChatSessionStore())
        calls = []

        async def process(*args, **kwargs):
            calls.append(1)
            return ChatResponse(text="", metadata={"wait_seconds": 0.01} if len(calls) == 1 else {})

        with patch.object(ChatService, "_process_locked", process):
            self.assertFalse(await asyncio.wait_for(service.process_and_send(request), timeout=2))
        self.assertEqual(len(calls), 2)


class PlannerRepositoryTests(unittest.IsolatedAsyncioTestCase):
    """规划样本按租户隔离且一轮只写一条。"""

    async def asyncSetUp(self) -> None:
        await base_models.init_database()
        await migrate.ensure_schema()
        self.tenant = f"planner-{uuid4().hex}"
        self.addAsyncCleanup(chat_repository.clear_tenant, self.tenant)

    async def test_sample_is_idempotent_and_tenant_scoped(self) -> None:
        sample = ChatPlannerSample(
            tenant=self.tenant,
            turn_id="turn-1",
            trigger_kind="mention",
            action="text_reply",
            label=1,
            features=json.dumps({"message_count": 1}),
        )
        await chat_repository.save_planner_sample(sample)
        await chat_repository.save_planner_sample(sample.model_copy(update={"action": "no_reply", "label": 0}))
        await chat_repository.save_planner_sample(sample.model_copy(update={"tenant": f"{self.tenant}-other"}))

        rows = await chat_repository.list_planner_samples(self.tenant)
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0].action, rows[0].label), ("text_reply", 1))
        other = await chat_repository.list_planner_samples(f"{self.tenant}-other")
        self.assertEqual(len(other), 1)
        await chat_repository.clear_tenant(f"{self.tenant}-other")


class PlannerConsoleTests(unittest.IsolatedAsyncioTestCase):
    """控制台按租户读取规划样本。"""

    async def test_list_and_validation(self) -> None:
        row = ChatPlannerSample(
            tenant="tenant",
            turn_id="turn-1",
            trigger_kind="mention",
            action="text_reply",
            label=1,
            features=json.dumps({"message_count": 2}),
            provider="fake",
            model="m",
        )
        with patch.object(
            chat_routes.chat_repository, "list_planner_samples", new=AsyncMock(return_value=[row])
        ) as listing:
            result = await chat_routes.list_planner_samples("tenant", 50)
        listing.assert_awaited_once_with("tenant", 50)
        self.assertEqual(result["items"][0]["action"], "text_reply")
        self.assertEqual(result["items"][0]["features"], {"message_count": 2})

        with self.assertRaises(HTTPException):
            await chat_routes.list_planner_samples("  ", 50)

        with (
            patch.object(chat_routes.chat_repository, "list_planner_samples", new=AsyncMock(side_effect=RuntimeError)),
            self.assertRaises(HTTPException) as caught,
        ):
            await chat_routes.list_planner_samples("tenant", 50)
        self.assertEqual(caught.exception.status_code, 503)


if __name__ == "__main__":
    unittest.main()
