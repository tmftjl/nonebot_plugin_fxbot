"""网页渠道的正常聊天链、鉴权、隔离、工具限制和取消回归。"""

from __future__ import annotations

import json
import asyncio
import unittest
from copy import deepcopy
from uuid import uuid4
from types import SimpleNamespace
from contextlib import asynccontextmanager
from unittest.mock import Mock, AsyncMock, patch

import httpx
from fastapi import FastAPI
from src.plugins.nonebot_plugin_fxbot.chat import hooks, service as service_module, session_hooks
from src.plugins.nonebot_plugin_fxbot.config import SYSTEM_DEFAULTS
from src.plugins.nonebot_plugin_fxbot.console import auth, chat_channel
from src.plugins.nonebot_plugin_fxbot.chat.tools import ToolSpec, ToolRuntime
from src.plugins.nonebot_plugin_fxbot.chat.session import ChatSessionStore
from src.plugins.nonebot_plugin_fxbot.chat.providers import LLMResponse
from src.plugins.nonebot_plugin_fxbot.console.routes import local_chat
from src.plugins.nonebot_plugin_fxbot.chat.heart_flow import conversation
from src.plugins.nonebot_plugin_fxbot.chat.tools.executor import execute_tool, tool_allowed
from src.plugins.nonebot_plugin_fxbot.chat.tools.registry import ToolRegistry
from src.plugins.nonebot_plugin_fxbot.chat.message_receive import sender


class ChannelTests(unittest.IsolatedAsyncioTestCase):
    """独立会话仍通过现有服务记录历史，不需要真实 Bot。"""

    def setUp(self) -> None:
        self.conversation = uuid4()
        self.message_id = uuid4()
        self.store = ChatSessionStore()
        self.service = service_module.ChatService(self.store)
        self.channel = chat_channel.ConsoleChatChannel(self.service)
        self.addAsyncCleanup(self.channel.stop)
        self.config = deepcopy(SYSTEM_DEFAULTS)
        self.config["chat"].update(enabled=True, rest_time_no_reply=False, max_private_context_size=20)
        manager = SimpleNamespace(get_system=lambda: self.config)
        self.enterContext(patch.object(service_module, "get_config_manager", return_value=manager))
        self.enterContext(patch.object(service_module.memory_state, "is_ready", return_value=False))
        self.enterContext(patch.object(self.service, "_hydrate", AsyncMock()))
        self.enterContext(patch.object(chat_channel, "can_reply", return_value=True))
        self.enterContext(patch.object(chat_channel, "load_personas", return_value={"test": "persona"}))
        self.enterContext(patch.object(chat_channel, "effective_persona_name", return_value="test"))
        self.enterContext(patch.object(chat_channel.provider_manager, "list_providers_by_type", return_value=["fake"]))
        self.enterContext(patch.object(chat_channel, "logger", Mock()))
        self.enterContext(patch.object(hooks, "_hooks", {}))
        self.enterContext(patch.object(session_hooks, "_policies", {}))
        self.addCleanup(session_hooks.register_session_policy("test-console", chat_channel._policy))
        self.bindings = {}
        self.enterContext(
            patch.object(chat_channel, "get_binding", side_effect=lambda kind, sid: self.bindings.get((kind, sid), ""))
        )
        self.enterContext(
            patch.object(
                chat_channel,
                "set_binding",
                side_effect=lambda kind, sid, value: self.bindings.__setitem__((kind, sid), value or ""),
            )
        )
        self.generation = self.enterContext(
            patch.object(self.service, "process_and_send", AsyncMock(return_value=False))
        )

    async def begin(self, **values):
        fields = dict(text="你好", message_id=self.message_id, nickname="控制台用户", persona="", provider="")
        fields.update(values)
        return await self.channel.begin(self.conversation, **fields)

    async def collect(self, turn):
        return [json.loads(line) async for line in self.channel.events(turn)]

    async def test_inbound_identity_is_independent_and_has_no_platform_authority(self) -> None:
        turn = await self.begin()
        events = await self.collect(turn)
        request, runtime = self.generation.await_args.args
        self.assertEqual(request.session_id, chat_channel.PREFIX + self.conversation.hex)
        self.assertEqual(request.user_id, self.conversation.hex)
        self.assertEqual(request.platform, "webui")
        self.assertIsNone(request.group_id)
        self.assertIsNone(runtime.bot)
        self.assertIsNone(runtime.event)
        self.assertEqual(runtime.allowed_tools, chat_channel.CHANNEL_TOOLS)
        self.assertTrue(request.metadata["history_recorded"])
        self.assertFalse(session_hooks.direct_reply_mode(request.session_id))
        self.assertEqual([event["type"] for event in events], ["accepted", "status", "done"])
        self.assertEqual((await self.channel.snapshot(self.conversation))["items"][0]["content"], "你好")
        self.assertEqual(self.channel.active, {})

    async def test_real_service_sends_through_guard_and_records_visible_history(self) -> None:
        async def send(request, runtime):
            return await self.service._send_generated(
                request, service_module.ChatResponse("实际正文", epoch=request.accepted_epoch), runtime
            )

        self.generation.side_effect = send
        with (
            patch.object(sender, "tts_enabled", return_value=False),
            patch.object(sender, "history_enabled", return_value=False),
            patch.object(sender, "can_reply", return_value=True),
        ):
            events = await self.collect(await self.begin())
        self.assertEqual([item["content"] for item in events if item["type"] == "message"], ["实际正文"])
        self.assertEqual(
            [item["role"] for item in (await self.channel.snapshot(self.conversation))["items"]], ["user", "assistant"]
        )

    async def test_normal_planner_replyer_and_sender_work_without_any_real_bot(self) -> None:
        planner = SimpleNamespace(
            provider_id="fake",
            chat=AsyncMock(
                side_effect=[
                    LLMResponse(
                        tool_calls=[
                            {
                                "id": "reply-call",
                                "type": "function",
                                "function": {
                                    "name": "reply",
                                    "arguments": json.dumps(
                                        {"msg_id": self.message_id.hex, "reply_reference": "自然回应问候"}
                                    ),
                                },
                            }
                        ]
                    ),
                    LLMResponse(
                        tool_calls=[
                            {"id": "stop-call", "type": "function", "function": {"name": "stop", "arguments": "{}"}}
                        ]
                    ),
                ]
            ),
        )
        replyer = SimpleNamespace(
            name="fake", provider_id="fake", chat=AsyncMock(return_value=LLMResponse(content="你好，我在这里。"))
        )

        @asynccontextmanager
        async def reply_scope(*args, **kwargs):
            yield replyer

        @asynccontextmanager
        async def planner_scope(*args, **kwargs):
            yield planner

        async def normal(request, runtime):
            return await service_module.ChatService.process_and_send(self.service, request, runtime)

        self.generation.side_effect = normal
        with (
            patch.object(service_module, "TaskProvider", reply_scope),
            patch.object(service_module, "candidate_providers", return_value=["fake"]),
            patch.object(service_module, "can_reply", return_value=True),
            patch.object(service_module, "_memory_block", AsyncMock(return_value="")),
            patch.object(service_module, "_learning_reference", AsyncMock(return_value="")),
            patch.object(service_module.prompt, "build_persona_text", return_value=("test", "人格")),
            patch.object(service_module.generator, "allowed_tools", AsyncMock(return_value=(set(), []))),
            patch.object(
                service_module.generator,
                "shape_reply",
                AsyncMock(
                    return_value=(
                        LLMResponse(content="你好，我在这里。"),
                        [SimpleNamespace(text="你好，我在这里。")],
                        "你好，我在这里。",
                    )
                ),
            ),
            patch.object(conversation, "fallback_provider", planner_scope),
            patch.object(conversation, "allowed_tools", AsyncMock(return_value=(set(), []))),
            patch.object(conversation, "planner_config", return_value={"passive_replyer": False}),
            patch.object(conversation, "planner_snapshot", return_value=None),
            patch.object(conversation, "record_plan_sample", AsyncMock()),
            patch.object(conversation, "end_turn_without_reply", AsyncMock()),
            patch.object(conversation, "can_reply", return_value=True),
            patch.object(sender, "can_reply", return_value=True),
            patch.object(sender, "history_enabled", return_value=False),
        ):
            events = await self.collect(await self.begin())
        self.assertEqual([item["content"] for item in events if item["type"] == "message"], ["你好，我在这里。"])
        self.assertEqual(planner.chat.await_count, 2)
        replyer.chat.assert_awaited_once()
        snapshot = await self.channel.snapshot(self.conversation)
        self.assertEqual([item["role"] for item in snapshot["items"]], ["user", "assistant"])

    async def test_send_guard_rejection_never_emits_or_records_assistant(self) -> None:
        self.addCleanup(hooks.register_chat_hook("send_guard", "deny", lambda *args: False))

        async def send(request, runtime):
            return await self.service._send_generated(
                request, service_module.ChatResponse("不应发送", epoch=request.accepted_epoch), runtime
            )

        self.generation.side_effect = send
        with patch.object(sender, "can_reply", return_value=True):
            events = await self.collect(await self.begin())
        self.assertFalse(any(event["type"] == "message" for event in events))
        self.assertEqual(len((await self.channel.snapshot(self.conversation))["items"]), 1)

    async def test_duplicate_message_is_not_generated_twice(self) -> None:
        await self.collect(await self.begin())
        with self.assertRaises(chat_channel.ChannelRejected) as caught:
            await self.begin()
        self.assertEqual(caught.exception.code, "duplicate")
        self.generation.assert_awaited_once()

    async def test_different_conversations_do_not_share_history(self) -> None:
        await self.collect(await self.begin())
        self.assertEqual((await self.channel.snapshot(uuid4()))["items"], [])

    async def test_new_runtime_restores_existing_database_history_for_same_webui_identity(self) -> None:
        from src.plugins.nonebot_plugin_fxbot.chat.memory.repository import chat_repository

        history = [
            {"role": "user", "content": "上次的问题", "_sender_id": self.conversation.hex},
            {"role": "assistant", "content": "上次的已发送回复", "_self_message": True},
        ]
        restarted_service = service_module.ChatService(ChatSessionStore())
        restarted = chat_channel.ConsoleChatChannel(restarted_service)
        with (
            patch.object(service_module.memory_state, "is_ready", return_value=True),
            patch.object(chat_repository, "get_session_state", AsyncMock(return_value=SimpleNamespace(generation=7))),
            patch.object(chat_repository, "get_recent_messages", AsyncMock(return_value=history)) as recent,
        ):
            state = await restarted.snapshot(self.conversation)
        self.assertEqual([item["content"] for item in state["items"]], ["上次的问题", "上次的已发送回复"])
        self.assertEqual(restarted_service.session_store.epoch(chat_channel.session_id(self.conversation)), 7)
        recent.assert_awaited_once_with(chat_channel.session_id(self.conversation), 10)

    async def test_snapshot_hides_internal_and_unsent_assistant_content(self) -> None:
        tenant = chat_channel.session_id(self.conversation)
        for message in (
            {"role": "assistant", "content": "generated but not sent"},
            {"role": "assistant", "content": "planner", "_planner_internal": True},
            {"role": "user", "content": "reference", "_reference": True},
            {"role": "assistant", "content": "visible", "_self_message": True},
        ):
            self.store.append(tenant, message)
        self.assertEqual(
            [item["content"] for item in (await self.channel.snapshot(self.conversation))["items"]], ["visible"]
        )

    async def test_ingress_rejection_prevents_recording_and_model(self) -> None:
        self.addCleanup(hooks.register_chat_hook("ingress_guard", "deny", lambda *args: False))
        with self.assertRaises(chat_channel.ChannelRejected):
            await self.begin()
        self.assertEqual(self.store.get(chat_channel.session_id(self.conversation)), [])
        self.generation.assert_not_called()

    async def test_concurrent_same_session_rejected_and_stale_stop_is_safe(self) -> None:
        started = asyncio.Event()

        async def wait(*args):
            started.set()
            await asyncio.Event().wait()

        self.generation.side_effect = wait
        turn = await self.begin()
        await asyncio.wait_for(started.wait(), 1)
        with self.assertRaises(chat_channel.ChannelRejected):
            await self.begin(message_id=uuid4())
        self.assertFalse(await self.channel.cancel(self.conversation, uuid4()))
        self.assertFalse(turn.task.done())
        self.assertTrue(await self.channel.cancel(self.conversation, self.message_id))
        self.assertEqual(self.channel.active, {})

    async def test_clearing_cancels_generation_and_does_not_clear_other_session(self) -> None:
        tenant = chat_channel.session_id(self.conversation)
        other = chat_channel.session_id(uuid4())
        self.store.append(other, {"role": "user", "content": "keep"})

        async def wait(*args):
            await asyncio.Event().wait()

        self.generation.side_effect = wait
        turn = await self.begin()
        await asyncio.sleep(0)
        await self.channel.clear(self.conversation)
        self.assertTrue(turn.task.done())
        self.assertEqual(self.store.get(tenant), [])
        self.assertEqual(self.store.get(other)[0]["content"], "keep")
        self.assertEqual(self.channel.active, {})

    async def test_closing_response_stream_cancels_its_task(self) -> None:
        async def wait(*args):
            await asyncio.Event().wait()

        self.generation.side_effect = wait
        turn = await self.begin()
        stream = self.channel.events(turn)
        await anext(stream)
        await stream.aclose()
        self.assertTrue(turn.task.done())
        self.assertEqual(self.channel.active, {})

    async def test_http_disconnect_before_body_iteration_releases_reserved_turn(self) -> None:
        app = FastAPI()
        app.include_router(local_chat.router)
        disconnected = asyncio.Event()
        request_read = False
        body = json.dumps({"text": "你好", "message_id": str(self.message_id)}).encode()

        async def receive():
            nonlocal request_read
            if not request_read:
                request_read = True
                return {"type": "http.request", "body": body, "more_body": False}
            await disconnected.wait()
            return {"type": "http.disconnect"}

        async def send(event):
            if event["type"] == "http.response.start":
                disconnected.set()
                await asyncio.sleep(0)

        async def wait(*args):
            await asyncio.Event().wait()

        self.generation.side_effect = wait
        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.0"},
            "http_version": "1.1",
            "method": "POST",
            "path": f"/local-chat/{self.conversation}/messages",
            "query_string": b"",
            "headers": [(b"authorization", b"Bearer test-token"), (b"content-type", b"application/json")],
            "scheme": "http",
            "server": ("test", 80),
            "client": ("test", 123),
            "root_path": "",
        }
        with (
            patch.object(local_chat, "console_chat", self.channel),
            patch.object(auth, "get_console_token", return_value="test-token"),
        ):
            await asyncio.wait_for(app(scope, receive, send), 2)
        self.assertEqual(self.channel.active, {})

    async def test_stop_rejects_late_send_even_when_provider_swallows_cancellation(self) -> None:
        started = asyncio.Event()

        async def resistant_provider(request, runtime):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await runtime.matcher.send("不应发送的迟到回复")

        self.generation.side_effect = resistant_provider
        turn = await self.begin()
        await asyncio.wait_for(started.wait(), 1)
        self.assertTrue(await self.channel.cancel(self.conversation, self.message_id))
        events = await self.collect(turn)
        self.assertFalse(any(event["type"] == "message" for event in events))

    async def test_clear_rejects_late_send_even_when_provider_swallows_cancellation(self) -> None:
        started = asyncio.Event()

        async def resistant_provider(request, runtime):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await runtime.matcher.send("不应发送的迟到回复")

        self.generation.side_effect = resistant_provider
        turn = await self.begin()
        await asyncio.wait_for(started.wait(), 1)
        await self.channel.clear(self.conversation)
        self.assertTrue(turn.cancelled)
        self.assertEqual((await self.channel.snapshot(self.conversation))["items"], [])

    async def test_clear_during_ingress_guard_rejects_stale_message(self) -> None:
        def clear(context, details):
            self.store.clear(context.session_id)

        self.addCleanup(hooks.register_chat_hook("ingress_guard", "clear", clear))
        with self.assertRaises(chat_channel.ChannelRejected) as caught:
            await self.begin()
        self.assertEqual(caught.exception.code, "stale")

    async def test_runtime_error_never_leaks_to_response(self) -> None:
        self.generation.side_effect = RuntimeError("secret internal path and upstream token")
        events = await self.collect(await self.begin())
        serialized = json.dumps(events, ensure_ascii=False)
        self.assertNotIn("secret", serialized)
        self.assertTrue(any(event["type"] == "error" for event in events))


class ConsoleAuthTests(unittest.IsolatedAsyncioTestCase):
    """所有读写与流式入口使用同一 Bearer 鉴权，UUID 不能覆盖真实租户。"""

    async def asyncSetUp(self) -> None:
        app = FastAPI()
        app.include_router(local_chat.router)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        self.addAsyncCleanup(self.client.aclose)
        self.enterContext(patch.object(auth, "get_console_token", return_value="test-token"))
        self.enterContext(patch.object(local_chat, "logger", Mock()))
        self.headers = {"Authorization": "Bearer test-token"}

    async def test_all_routes_require_bearer_auth(self) -> None:
        identifier = str(uuid4())
        paths = [
            ("GET", "/local-chat/options"),
            ("GET", f"/local-chat/{identifier}"),
            ("POST", f"/local-chat/{identifier}/messages"),
            ("POST", f"/local-chat/{identifier}/stop"),
            ("DELETE", f"/local-chat/{identifier}"),
        ]
        for method, path in paths:
            with self.subTest(path=path):
                self.assertEqual((await self.client.request(method, path)).status_code, 401)
                self.assertEqual(
                    (await self.client.request(method, path, headers={"Authorization": "Bearer wrong"})).status_code,
                    403,
                )
        with (
            patch.object(local_chat, "load_personas", return_value={"test": "text"}),
            patch.object(local_chat.provider_manager, "list_providers_by_type", return_value=[]),
        ):
            self.assertEqual((await self.client.get("/local-chat/options", headers=self.headers)).status_code, 200)

    async def test_uuid_and_payload_size_validated_before_model(self) -> None:
        response = await self.client.get("/local-chat/qq:bot:group:123", headers=self.headers)
        self.assertEqual(response.status_code, 422)
        response = await self.client.post(
            f"/local-chat/{uuid4()}/messages",
            headers=self.headers,
            json={"text": "x" * 8001, "message_id": str(uuid4())},
        )
        self.assertEqual(response.status_code, 422)

    async def test_route_hides_runtime_errors(self) -> None:
        with patch.object(local_chat.console_chat, "snapshot", AsyncMock(side_effect=RuntimeError("secret"))):
            response = await self.client.get(f"/local-chat/{uuid4()}", headers=self.headers)
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("secret", response.text)


class TransportToolTests(unittest.IsolatedAsyncioTestCase):
    """渠道限制是拒绝型交集，不能靠模型工具名或空权限绕过。"""

    async def test_transport_allowlist_applies_at_discovery_and_execution(self) -> None:
        handler = Mock(return_value="must not run")
        spec = ToolSpec("unsafe", "test", {}, handler)
        runtime = ToolRuntime(allowed_tools=frozenset({"safe"}))
        self.assertFalse(await tool_allowed(spec, runtime, session_id="webui"))
        registry = ToolRegistry()
        registry.register(spec)
        from src.plugins.nonebot_plugin_fxbot.chat.tools import ToolContext

        result = await execute_tool(
            "unsafe", {}, ToolContext(session_id="webui"), runtime, allowed_names={"unsafe"}, registry=registry
        )
        self.assertFalse(result["ok"])
        handler.assert_not_called()
