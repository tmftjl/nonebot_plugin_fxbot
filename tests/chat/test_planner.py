"""P5-1 规划：动作解析、执行分派、样本落库与控制台追溯。"""

from __future__ import annotations

import json
import unittest
from uuid import uuid4
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from sqlmodel import select
from src.plugins.nonebot_plugin_fxbot.db import base_models
from src.plugins.nonebot_plugin_fxbot.chat import service as service_module
from src.plugins.nonebot_plugin_fxbot.chat.tools import ToolRuntime
from src.plugins.nonebot_plugin_fxbot.chat.types import ChatRequest
from src.plugins.nonebot_plugin_fxbot.chat.memory import state as memory_state, migrate
from src.plugins.nonebot_plugin_fxbot.chat.planner import (
    Plan,
    parse_plan,
    request_plan,
    plan_features,
)
from src.plugins.nonebot_plugin_fxbot.chat.service import ChatService, _PlanContext
from src.plugins.nonebot_plugin_fxbot.chat.session import ChatSessionStore
from src.plugins.nonebot_plugin_fxbot.chat.providers import LLMResponse, provider_manager
from src.plugins.nonebot_plugin_fxbot.console.routes import chat as chat_routes
from src.plugins.nonebot_plugin_fxbot.chat.memory.models import ChatTurn, ChatPlannerSample
from src.plugins.nonebot_plugin_fxbot.chat.memory.repository import chat_repository


class _FakeConfig:
    """只提供对话与规划需要的配置。"""

    def __init__(self, planner: dict[str, object] | None = None) -> None:
        self.planner = planner or {"enabled": False, "mode": "simple", "max_rounds": 1, "wait_seconds": 60}

    def get_system(self) -> dict[str, object]:
        return {
            "chat": {
                "max_history": 10,
                "image_description": False,
                "persona": "",
                "provider": "",
                "enabled_tools": [],
                "memory": {"history_enabled": True},
                "model_routing": {"planner": ""},
                "planner": self.planner,
            }
        }


class _PlannerProvider:
    """规划返回固定动作，回复返回固定文本。"""

    provider_id = "fake"

    def __init__(self, plan: object) -> None:
        self.plan = plan
        self.plan_calls = 0
        self.reply_messages: list[dict[str, object]] | None = None

    def get_model(self) -> str:
        return "fake-model"

    async def chat_json(self, messages: list[dict[str, object]], schema: object = None, model: str | None = None):
        self.plan_calls += 1
        if isinstance(self.plan, Exception):
            raise self.plan
        return self.plan

    async def chat(self, messages: list[dict[str, object]], tools: object = None, **kwargs: object) -> LLMResponse:
        self.reply_messages = messages
        return LLMResponse(content="回复")


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


class PlanParsingTests(unittest.TestCase):
    """规划输出的动作与边界校验。"""

    def test_simple_actions(self) -> None:
        for action in ("text_reply", "no_reply", "emoji_reply", "end_conversation"):
            with self.subTest(action=action):
                plan = parse_plan({"action": action}, [])
                assert plan is not None
                self.assertEqual(plan.action, action)
                self.assertEqual(plan.label, 1 if action in {"text_reply", "emoji_reply"} else 0)

    def test_wait_is_clamped(self) -> None:
        self.assertEqual(parse_plan({"action": "wait", "wait_seconds": 5}, []).wait_seconds, 30)  # type: ignore[union-attr]
        self.assertEqual(parse_plan({"action": "wait", "wait_seconds": 500}, []).wait_seconds, 120)  # type: ignore[union-attr]
        self.assertEqual(parse_plan({"action": "wait"}, []).wait_seconds, 60)  # type: ignore[union-attr]

    def test_tool_requires_available_name(self) -> None:
        plan = parse_plan({"action": "tool:web_search", "arguments": {"query": "x"}}, ["web_search"])
        assert plan is not None
        self.assertEqual((plan.action, plan.tool, plan.arguments), ("tool", "web_search", {"query": "x"}))
        self.assertIsNone(parse_plan({"action": "tool:shell"}, ["web_search"]))
        self.assertIsNone(parse_plan({"action": "tool:"}, ["web_search"]))

    def test_fetch_knowledge_requires_tool(self) -> None:
        self.assertIsNone(parse_plan({"action": "fetch_knowledge", "query": "x"}, ["web_search"]))
        plan = parse_plan({"action": "fetch_knowledge", "query": "x"}, ["search_knowledge"])
        assert plan is not None
        self.assertEqual(plan.tool, "search_knowledge")
        self.assertEqual(plan.arguments, {"query": "x"})

    def test_invalid_payloads(self) -> None:
        for payload in (None, "", "not json", {"action": "dance"}, []):
            with self.subTest(payload=payload):
                self.assertIsNone(parse_plan(payload, []))

    def test_features_are_lightweight(self) -> None:
        request = _request(
            "tenant",
            text="在吗？",
            batched=True,
            source_message_ids=["m1", "m2"],
            source_author_ids=["u1", "u2"],
        )
        features = plan_features(request, history_len=4, memory_chars=80)
        self.assertEqual(features["message_count"], 2)
        self.assertEqual(features["authors"], 2)
        self.assertTrue(features["has_question"])
        self.assertEqual(features["history_len"], 4)
        self.assertEqual(features["memory_chars"], 80)


class RequestPlanTests(unittest.IsolatedAsyncioTestCase):
    """规划调用失败时重试一次。"""

    async def test_retries_after_failure(self) -> None:
        class Flaky(_PlannerProvider):
            async def chat_json(self, messages, schema=None, model=None):
                self.plan_calls += 1
                if self.plan_calls == 1:
                    raise RuntimeError("network")
                return {"action": "text_reply"}

        provider = Flaky(None)
        plan = await request_plan(provider, [{"role": "user", "content": "hi"}], [], model="m")
        assert plan is not None
        self.assertEqual(plan.action, "text_reply")
        self.assertEqual(provider.plan_calls, 2)

    async def test_returns_none_when_all_attempts_fail(self) -> None:
        provider = _PlannerProvider(RuntimeError("network"))
        self.assertIsNone(await request_plan(provider, [{"role": "user", "content": "hi"}], []))
        self.assertEqual(provider.plan_calls, 2)


class PlannerServiceTests(unittest.IsolatedAsyncioTestCase):
    """真实服务下规划动作对回复与样本的影响。"""

    async def asyncSetUp(self) -> None:
        await base_models.init_database()
        await migrate.ensure_schema()
        memory_state.mark_ready()
        self.tenant = f"planner-{uuid4().hex}"
        self.addAsyncCleanup(chat_repository.clear_tenant, self.tenant)

    async def _run(self, request: ChatRequest, plan: object, planner: dict[str, object] | None = None):
        provider = _PlannerProvider(plan)
        service = ChatService(ChatSessionStore())
        config = _FakeConfig(planner)
        with (
            patch.object(service_module, "get_config_manager", return_value=config),
            patch.object(service_module, "planner_config", return_value=config.planner),
            patch.object(provider_manager, "lease", _lease(provider)),
        ):
            response = await service.process(request)
        return service, provider, response

    async def _turn_status(self, request: ChatRequest) -> str:
        async with base_models.async_maker() as session:
            rows = await ChatTurn.select_rows(tenant=request.session_id, turn_key=request.turn_id, session=session)
        return rows[0].status if rows else ""

    async def _samples(self, tenant: str) -> list[ChatPlannerSample]:
        async with base_models.async_maker() as session:
            statement = select(ChatPlannerSample).where(ChatPlannerSample.tenant == tenant)
            return list((await session.execute(statement)).scalars().all())

    async def test_planner_disabled_does_not_call_model(self) -> None:
        request = _request(self.tenant)
        service, provider, response = await self._run(request, {"action": "no_reply"})
        self.assertEqual(provider.plan_calls, 0)
        self.assertEqual(response.text, "回复")
        self.assertFalse(await self._samples(self.tenant))

    async def test_simple_mode_ignores_enabled_planner(self) -> None:
        request = _request(self.tenant)
        provider = _PlannerProvider({"action": "no_reply"})
        service = ChatService(ChatSessionStore())
        config = _FakeConfig({"enabled": True, "mode": "simple"})
        with (
            patch.object(service_module, "get_config_manager", return_value=config),
            patch.object(service_module, "planner_config", return_value=config.planner),
            patch.object(provider_manager, "lease", _lease(provider)),
        ):
            response = await service.process(request)
        self.assertEqual(provider.plan_calls, 0)
        self.assertEqual(response.text, "回复")

    async def test_text_reply_records_reply_label(self) -> None:
        request = _request(self.tenant)
        _service, provider, response = await self._run(
            request,
            {"action": "text_reply"},
            {"enabled": True, "mode": "complex"},
        )
        self.assertEqual(provider.plan_calls, 1)
        self.assertEqual(provider.reply_messages is not None, True)
        self.assertEqual(response.metadata.get("plan_action"), "text_reply")
        samples = await self._samples(self.tenant)
        self.assertEqual(len(samples), 1)
        self.assertEqual((samples[0].action, samples[0].label), ("text_reply", 1))
        self.assertEqual(samples[0].model, "fake-model")

    async def test_no_reply_ends_turn_without_assistant_history(self) -> None:
        request = _request(self.tenant)
        service, _provider, response = await self._run(
            request,
            {"action": "no_reply"},
            {"enabled": True, "mode": "complex"},
        )
        self.assertEqual(response.text, "")
        self.assertEqual(response.metadata.get("plan_action"), "no_reply")
        self.assertEqual(await self._turn_status(request), "ended")
        self.assertEqual(service.session_store.get(self.tenant), [{"role": "user", "content": "Alice(user): 你好"}])
        samples = await self._samples(self.tenant)
        self.assertEqual((samples[0].action, samples[0].label), ("no_reply", 0))

    async def test_end_conversation_is_silent(self) -> None:
        request = _request(self.tenant)
        _service, _provider, response = await self._run(
            request,
            {"action": "end_conversation"},
            {"enabled": True, "mode": "complex"},
        )
        self.assertEqual(response.text, "")
        self.assertEqual(await self._turn_status(request), "ended")

    async def test_planner_failure_falls_back_to_reply(self) -> None:
        request = _request(self.tenant)
        _service, provider, response = await self._run(
            request,
            RuntimeError("network"),
            {"enabled": True, "mode": "complex"},
        )
        self.assertEqual(provider.plan_calls, 2)
        self.assertEqual(response.text, "回复")
        self.assertFalse(response.metadata.get("plan_action"))
        self.assertFalse(await self._samples(self.tenant))

    async def test_emoji_reply_adds_instruction(self) -> None:
        request = _request(self.tenant)
        _service, provider, response = await self._run(
            request,
            {"action": "emoji_reply"},
            {"enabled": True, "mode": "complex"},
        )
        self.assertEqual(response.metadata.get("plan_action"), "emoji_reply")
        assert provider.reply_messages is not None
        self.assertTrue(any("只回一个自然的表情" in str(message.get("content")) for message in provider.reply_messages))

    async def test_tool_action_goes_through_executor(self) -> None:
        request = _request(self.tenant)
        executor = AsyncMock(return_value={"ok": True, "result": "搜索完成"})
        with patch.object(service_module, "execute_tool", executor):
            _service, provider, response = await self._run(
                request,
                {"action": "tool:web_search", "arguments": {"query": "天气"}},
                {"enabled": True, "mode": "complex"},
            )
        executor.assert_awaited_once()
        self.assertEqual(executor.await_args.args[0], "web_search")
        self.assertEqual(response.text, "回复")
        assert provider.reply_messages is not None
        self.assertTrue(any("搜索完成" in str(message.get("content")) for message in provider.reply_messages))

    async def test_wait_rebuild_merges_new_messages(self) -> None:
        request = _request(self.tenant)
        service = ChatService(ChatSessionStore())
        entry = {"role": "user", "content": "Alice(user): 你好"}
        service.session_store.append(self.tenant, entry)
        service.session_store.append(self.tenant, {"role": "user", "content": "Bob(bob): 新消息"})
        context = _PlanContext(
            request=request,
            messages=[],
            entry=entry,
            epoch=service.session_store.epoch(self.tenant),
            runtime=ToolRuntime(),
            allowed_names=set(),
            persona_text="人格",
            memory_block="记忆",
            current_content="Alice(user): 你好",
            history_budget=12000,
            limit=10,
            provider_name="",
        )
        outcome = await service._plan_wait(context, Plan("wait", wait_seconds=0))
        self.assertFalse(outcome.finished)
        assert outcome.messages is not None
        joined = "\n".join(str(message.get("content")) for message in outcome.messages)
        self.assertIn("Bob(bob): 新消息", joined)
        self.assertEqual(joined.count("Alice(user): 你好"), 1)

    async def test_wait_aborts_when_new_trigger_arrives(self) -> None:
        request = _request(self.tenant, trigger_seq=1)
        service = ChatService(ChatSessionStore())
        service.session_store.next_seq(self.tenant, request.user_id)
        service.session_store.next_seq(self.tenant, request.user_id)
        context = _PlanContext(
            request=request,
            messages=[],
            entry={},
            epoch=0,
            runtime=ToolRuntime(),
            allowed_names=set(),
            persona_text="",
            memory_block="",
            current_content="",
            history_budget=12000,
            limit=10,
            provider_name="",
        )
        outcome = await service._plan_wait(context, Plan("wait", wait_seconds=0))
        self.assertTrue(outcome.finished)


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
