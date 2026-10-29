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
from sqlmodel import select
from src.plugins.nonebot_plugin_fxbot.db import base_models
from src.plugins.nonebot_plugin_fxbot.chat import planner as planner_module, service as service_module
from src.plugins.nonebot_plugin_fxbot.chat.tools import ToolRuntime
from src.plugins.nonebot_plugin_fxbot.chat.types import ChatRequest, ChatResponse
from src.plugins.nonebot_plugin_fxbot.chat.memory import state as memory_state, migrate
from src.plugins.nonebot_plugin_fxbot.chat.planner import (
    parse_plan,
    build_prompt,
    request_plan,
    plan_features,
)
from src.plugins.nonebot_plugin_fxbot.chat.replyer import generator
from src.plugins.nonebot_plugin_fxbot.chat.service import ChatService
from src.plugins.nonebot_plugin_fxbot.chat.session import ChatSessionStore
from src.plugins.nonebot_plugin_fxbot.chat.providers import LLMResponse, provider_manager
from src.plugins.nonebot_plugin_fxbot.console.routes import chat as chat_routes
from src.plugins.nonebot_plugin_fxbot.chat.heart_flow import plan_flow
from src.plugins.nonebot_plugin_fxbot.chat.memory.models import ChatTurn, ChatPlannerSample
from src.plugins.nonebot_plugin_fxbot.chat.memory.repository import chat_repository
from src.plugins.nonebot_plugin_fxbot.chat.heart_flow.plan_flow import PlanContext as _PlanContext


class _FakeConfig:
    """只提供对话与规划需要的配置。"""

    def __init__(self, planner: dict[str, object] | None = None) -> None:
        self.planner = planner or {"mode": "simple", "wait_seconds": 60}

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

    def test_invalid_payloads(self) -> None:
        for payload in (None, "", "not json", {"action": "dance"}, []):
            with self.subTest(payload=payload):
                self.assertIsNone(parse_plan(payload, []))

    def test_reply_reference_is_parsed(self) -> None:
        plan = parse_plan({"action": "text_reply", "reply_reference": "  顺着话题接一句  "}, [])
        assert plan is not None
        self.assertEqual(plan.reply_reference, "顺着话题接一句")
        self.assertEqual(parse_plan({"action": "text_reply"}, []).reply_reference, "")  # type: ignore[union-attr]

    def test_passive_prompt_asks_for_guidance(self) -> None:
        passive_note = build_prompt([], [], passive=True)[-1]["content"]
        self.assertIn("reply_reference", passive_note)
        self.assertNotIn("reply_reference", build_prompt([], [], passive=False)[-1]["content"])

    def test_tool_definitions_show_parameters(self) -> None:
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "web_search",
                    "description": "联网搜索",
                    "parameters": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}, "count": {"type": "integer"}},
                        "required": ["query"],
                    },
                },
            }
        ]
        note = build_prompt([], tools)[-1]["content"]
        self.assertIn("- web_search: 联网搜索", note)
        self.assertIn("query(string)*", note)
        self.assertIn("count(integer)", note)

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

    async def test_accepts_tool_definitions(self) -> None:
        provider = _PlannerProvider({"action": "tool:web_search", "arguments": {"query": "x"}})
        tools = [{"type": "function", "function": {"name": "web_search", "description": "联网搜索", "parameters": {}}}]
        plan = await request_plan(provider, [{"role": "user", "content": "hi"}], tools)
        assert plan is not None
        self.assertEqual((plan.action, plan.tool, plan.arguments), ("tool", "web_search", {"query": "x"}))


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
            patch.object(plan_flow, "planner_config", return_value=config.planner),
            patch.object(provider_manager, "lease", _lease(provider)),
            patch.object(generator, "web_runtime", return_value=SimpleNamespace(enabled=True)),
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

    async def test_simple_mode_does_not_call_model(self) -> None:
        request = _request(self.tenant)
        service, provider, response = await self._run(request, {"action": "no_reply"})
        self.assertEqual(provider.plan_calls, 0)
        self.assertEqual(response.text, "回复")
        self.assertFalse(await self._samples(self.tenant))

    async def test_simple_mode_skips_planner(self) -> None:
        request = _request(self.tenant)
        provider = _PlannerProvider({"action": "no_reply"})
        service = ChatService(ChatSessionStore())
        config = _FakeConfig({"mode": "simple"})
        with (
            patch.object(service_module, "get_config_manager", return_value=config),
            patch.object(plan_flow, "planner_config", return_value=config.planner),
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
            {"mode": "complex"},
        )
        self.assertEqual(provider.plan_calls, 1)
        self.assertEqual(provider.reply_messages is not None, True)
        self.assertEqual(response.metadata.get("plan_action"), "text_reply")
        samples = await self._samples(self.tenant)
        self.assertEqual(len(samples), 1)
        self.assertEqual((samples[0].action, samples[0].label), ("text_reply", 1))
        self.assertEqual(samples[0].model, "fake-model")

    async def test_silent_and_wait_outcomes(self) -> None:
        for action in ("no_reply", "end_conversation", "wait"):
            with self.subTest(action=action):
                request = _request(self.tenant)
                context = SimpleNamespace(request=request, provider_name="fake")
                plan = planner_module.Plan(action, wait_seconds=30)
                if action == "wait":
                    outcome = await plan_flow.plan_wait(None, context, plan)
                    self.assertEqual(outcome.pause, 30)
                else:
                    with patch.object(plan_flow, "end_turn_without_reply", AsyncMock()):
                        outcome = await plan_flow.plan_silent(None, context, plan)
                    self.assertTrue(outcome.finished)

    async def test_no_reply_ends_turn_without_assistant_history(self) -> None:
        request = _request(self.tenant)
        service, _provider, response = await self._run(
            request,
            {"action": "no_reply"},
            {"mode": "complex"},
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
            {"mode": "complex"},
        )
        self.assertEqual(response.text, "")
        self.assertEqual(await self._turn_status(request), "ended")

    async def test_planner_failure_falls_back_to_reply(self) -> None:
        request = _request(self.tenant)
        _service, provider, response = await self._run(
            request,
            RuntimeError("network"),
            {"mode": "complex"},
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
            {"mode": "complex"},
        )
        self.assertEqual(response.metadata.get("plan_action"), "emoji_reply")
        assert provider.reply_messages is not None
        self.assertTrue(any("只回一个自然的表情" in str(message.get("content")) for message in provider.reply_messages))

    async def test_passive_replyer_injects_guidance(self) -> None:
        request = _request(self.tenant)
        _service, provider, response = await self._run(
            request,
            {"action": "text_reply", "reply_reference": "顺着爬山话题接一句"},
            {"mode": "complex", "passive_replyer": True},
        )
        self.assertEqual(response.text, "回复")
        assert provider.reply_messages is not None
        self.assertTrue(any("顺着爬山话题接一句" in str(message.get("content")) for message in provider.reply_messages))

    async def test_guidance_is_ignored_when_passive_off(self) -> None:
        request = _request(self.tenant)
        _service, provider, _response = await self._run(
            request,
            {"action": "text_reply", "reply_reference": "不该出现"},
            {"mode": "complex"},
        )
        assert provider.reply_messages is not None
        self.assertFalse(any("不该出现" in str(message.get("content")) for message in provider.reply_messages))

    async def test_tool_action_goes_through_executor(self) -> None:
        request = _request(self.tenant)
        executor = AsyncMock(return_value={"ok": True, "result": "搜索完成"})
        with patch.object(plan_flow, "execute_tool", executor):
            _service, provider, response = await self._run(
                request,
                {"action": "tool:web_search", "arguments": {"query": "天气"}},
                {"mode": "complex"},
            )
        executor.assert_awaited_once()
        self.assertEqual(executor.await_args.args[0], "web_search")
        self.assertEqual(response.text, "回复")
        assert provider.reply_messages is not None
        self.assertTrue(any("搜索完成" in str(message.get("content")) for message in provider.reply_messages))

    async def test_rebuild_merges_new_messages(self) -> None:
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
            limit=10,
            provider_name="",
        )
        messages = await plan_flow.rebuild_messages(service, context)
        joined = "\n".join(str(message.get("content")) for message in messages)
        self.assertIn("Bob(bob): 新消息", joined)
        self.assertEqual(joined.count("Alice(user): 你好"), 1)

    async def test_wait_is_aborted_when_new_trigger_arrives(self) -> None:
        request = _request(self.tenant, trigger_seq=1, accepted_epoch=0)
        service = ChatService(ChatSessionStore())
        service.session_store.next_seq(self.tenant, request.user_id)
        service.session_store.next_seq(self.tenant, request.user_id)
        with patch.object(ChatService, "_mark_turn_aborted", AsyncMock()) as aborted:
            self.assertFalse(await service._pause_turn(request, 5))
        aborted.assert_awaited_once()

    async def test_wait_replans_before_replying(self) -> None:
        request = _request(self.tenant)

        class _SeqPlanner(_PlannerProvider):
            async def chat_json(self, messages, schema=None, model=None):
                self.plan_calls += 1
                return {"action": "wait", "wait_seconds": 0} if self.plan_calls == 1 else {"action": "text_reply"}

        provider = _SeqPlanner({"action": "text_reply"})
        service = ChatService(ChatSessionStore())
        config = _FakeConfig({"mode": "complex", "wait_seconds": 60})
        with (
            patch.object(service_module, "get_config_manager", return_value=config),
            patch.object(plan_flow, "planner_config", return_value=config.planner),
            patch.object(provider_manager, "lease", _lease(provider)),
            patch.object(planner_module, "_WAIT_MIN", 0),
        ):
            response = await service.process(request)
        self.assertEqual(provider.plan_calls, 2)
        self.assertEqual(response.metadata.get("plan_action"), "text_reply")
        self.assertEqual(response.text, "回复")

    async def test_wait_releases_turn_lock(self) -> None:
        request = _request(self.tenant)
        service = ChatService(ChatSessionStore())
        calls: list[int] = []

        async def fake_process(_self: object, _request: ChatRequest, _runtime: object, **_kwargs: object):
            calls.append(1)
            if len(calls) == 1:
                return ChatResponse(text="", epoch=0, metadata={"wait_seconds": 0.5})
            return ChatResponse(text="", epoch=0)

        with patch.object(ChatService, "_process_locked", fake_process):
            task = asyncio.create_task(service.process_and_send(request))
            await asyncio.sleep(0.05)
            lock = service.session_store.turn_lock(request.session_id)
            self.assertFalse(lock.locked())
            await asyncio.wait_for(lock.acquire(), timeout=0.5)
            lock.release()
            self.assertFalse(await asyncio.wait_for(task, timeout=2))
        self.assertEqual(len(calls), 2)

    async def test_new_message_wakes_the_wait(self) -> None:
        request = _request(self.tenant)
        service = ChatService(ChatSessionStore())

        async def fake_process(_self: object, _request: ChatRequest, _runtime: object, **_kwargs: object):
            if not _request.metadata.get("waited"):
                _request.metadata["waited"] = True
                return ChatResponse(text="", epoch=0, metadata={"wait_seconds": 5})
            return ChatResponse(text="", epoch=0)

        with (
            patch.object(ChatService, "_process_locked", fake_process),
            patch.object(plan_flow, "steering_enabled", return_value=True),
        ):
            task = asyncio.create_task(service.process_and_send(request))
            await asyncio.sleep(0.05)
            service.session_store.mark_seen(request.session_id, "msg:new")
            self.assertFalse(await asyncio.wait_for(task, timeout=2))

    async def test_new_message_during_planning_replans(self) -> None:
        request = _request(self.tenant)
        store = ChatSessionStore()
        store.mark_seen(self.tenant, "msg:setup")

        class _Interrupted(_PlannerProvider):
            async def chat_json(self, messages, schema=None, model=None):
                self.plan_calls += 1
                if self.plan_calls == 1:
                    store.mark_seen(self.tenant, "msg:other")
                    await asyncio.sleep(0.4)
                    return {"action": "no_reply"}
                return {"action": "text_reply"}

        provider = _Interrupted({"action": "text_reply"})
        service = ChatService(store)
        config = _FakeConfig({"mode": "complex"})
        with (
            patch.object(service_module, "get_config_manager", return_value=config),
            patch.object(plan_flow, "planner_config", return_value=config.planner),
            patch.object(provider_manager, "lease", _lease(provider)),
            patch.object(plan_flow, "steering_enabled", return_value=True),
        ):
            response = await service.process(request)
        self.assertEqual(provider.plan_calls, 2)
        self.assertEqual(response.metadata.get("plan_action"), "text_reply")
        self.assertEqual(response.text, "回复")


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
