"""聊天扩展点默认行为、失败封闭、快照隔离和真实调用链接线。"""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from dataclasses import FrozenInstanceError
from unittest.mock import Mock, AsyncMock, patch

from src.plugins.nonebot_plugin_fxbot.chat import hooks, service as service_module
from src.plugins.nonebot_plugin_fxbot.chat.tools import (
    ToolSpec,
    ToolContext,
    ToolRuntime,
    ToolRegistry,
    search as search_module,
    executor,
)
from src.plugins.nonebot_plugin_fxbot.chat.types import ChatRequest, ChatResponse
from src.plugins.nonebot_plugin_fxbot.chat.replyer import prompt
from src.plugins.nonebot_plugin_fxbot.chat.session import ChatSessionStore
from src.plugins.nonebot_plugin_fxbot.chat.providers import LLMResponse
from src.plugins.nonebot_plugin_fxbot.chat.message_receive import sender


class ChatHooksTests(unittest.IsolatedAsyncioTestCase):
    """插件通过返回值干预流程，不接收可变身份或历史对象。"""

    def setUp(self) -> None:
        self.enterContext(patch.object(hooks, "_hooks", {}))
        self.request = ChatRequest("test:bot:private:user", "user", None, "你好", "turn", "m1", "private")
        self.context = hooks.hook_context(self.request)

    def register(self, kind, callback, name="test"):
        self.addCleanup(hooks.register_chat_hook(kind, name, callback))

    async def test_defaults_preserve_behavior(self) -> None:
        self.assertTrue(await hooks.guard("request_guard", self.context))
        self.assertEqual(hooks.resolve_persona(self.context, "名字", "人格"), ("名字", "人格"))
        self.assertEqual(hooks.prompt_notes(self.context), [])
        self.assertEqual(hooks.filter_reply_text(self.context, "正文"), "正文")
        await hooks.notify("test", self.context)

    async def test_guard_rejection_cannot_be_overridden(self) -> None:
        self.register("request_guard", lambda *args: False)
        later = Mock(return_value=True)
        self.register("request_guard", later, "later")
        self.assertFalse(await hooks.guard("request_guard", self.context))
        later.assert_not_called()

    async def test_guard_failures_and_bad_returns_deny(self) -> None:
        for callback in (Mock(side_effect=RuntimeError("down")), lambda *args: "yes", lambda *args: 1):
            self.register("send_guard", callback)
            self.assertFalse(await hooks.guard("send_guard", self.context))

    async def test_timeout_denies_but_cancellation_is_not_swallowed(self) -> None:
        async def slow(*args):
            await asyncio.Event().wait()

        self.register("request_guard", slow)
        with patch.object(hooks, "HOOK_TIMEOUT_SECONDS", 0.01):
            self.assertFalse(await hooks.guard("request_guard", self.context))
        task = asyncio.create_task(hooks.guard("request_guard", self.context))
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_nested_details_and_context_are_readonly(self) -> None:
        def deny_mutation(context, details):
            with self.assertRaises(FrozenInstanceError):
                context.user_id = "other"
            with self.assertRaises(TypeError):
                details["arguments"]["path"] = "other"
            with self.assertRaises(AttributeError):
                details["arguments"]["items"].append("other")

        self.register("tool_guard", deny_mutation)
        arguments = {"path": "original", "items": ["a"]}
        self.assertTrue(await hooks.guard("tool_guard", self.context, arguments=arguments))
        self.assertEqual(arguments, {"path": "original", "items": ["a"]})

    async def test_observer_failure_does_not_skip_next(self) -> None:
        self.register("observer", Mock(side_effect=RuntimeError("down")))
        later = AsyncMock()
        self.register("observer", later, "later")
        await hooks.notify("session_clear_complete", self.context, epoch=1)
        later.assert_awaited_once()
        self.assertEqual(later.await_args.args[2]["epoch"], 1)

    def test_unregister_old_implementation_is_safe(self) -> None:
        remove = hooks.register_chat_hook("reply_text", "plugin", lambda context, text: "old")
        self.register("reply_text", lambda context, text: "new", "plugin")
        remove()
        remove()
        self.assertEqual(hooks.filter_reply_text(self.context, "input"), "new")

    def test_sync_filter_types_and_errors_stop_generation(self) -> None:
        for kind, run in (
            ("persona", lambda: hooks.resolve_persona(self.context, "n", "t")),
            ("prompt", lambda: hooks.prompt_notes(self.context)),
            ("reply_text", lambda: hooks.filter_reply_text(self.context, "t")),
        ):
            self.register(kind, lambda *args: {})
            with self.assertRaises(hooks.ChatHookError):
                run()

    def test_persona_name_override_loads_corresponding_text(self) -> None:
        self.register("persona", lambda *args: hooks.PersonaOverride(name="运维"))
        with patch("src.plugins.nonebot_plugin_fxbot.chat.personas.get_persona_text", return_value="新人格"):
            self.assertEqual(hooks.resolve_persona(self.context, "原名", "原文"), ("运维", "新人格"))

    def test_persona_and_prompt_are_wired_without_changing_identity(self) -> None:
        self.register("persona", lambda *args: hooks.PersonaOverride("运维", "只讨论运维"))
        self.register("prompt", lambda context: "插件补充")
        with patch.object(prompt, "get_persona_text", return_value="原文"):
            name, text = prompt.build_persona_text(self.request, {"persona": "原名"}, {})
        self.assertEqual(name, "运维")
        self.assertIn("只讨论运维", text)
        self.assertIn("一对一私聊", text)
        messages = prompt.assemble_messages(self.request, [], text, "", "你好")
        self.assertEqual(messages[-1], {"role": "user", "content": "插件补充"})
        self.assertEqual(self.request.user_id, "user")

    async def test_request_guard_stops_model_and_clear_during_guard_is_safe(self) -> None:
        service = service_module.ChatService(ChatSessionStore())
        process = AsyncMock(return_value=ChatResponse(text="reply"))
        config = SimpleNamespace(get_system=lambda: {"chat": {"max_private_context_size": 10}})
        self.register("request_guard", lambda *args: False)
        with (
            patch.object(service_module, "get_config_manager", return_value=config),
            patch.object(service_module, "can_reply", return_value=True),
            patch.object(service, "_hydrate", AsyncMock()),
            patch.object(service, "_process", process),
        ):
            response = await service._process_locked(self.request, None)
        self.assertEqual(response.text, "")
        process.assert_not_awaited()

    async def test_clear_during_request_guard_cannot_start_old_generation(self) -> None:
        service = service_module.ChatService(ChatSessionStore())

        async def clear(context, details):
            service.session_store.clear(context.session_id)
            return True

        self.register("request_guard", clear)
        process = AsyncMock(return_value=ChatResponse(text="reply"))
        config = SimpleNamespace(get_system=lambda: {"chat": {"max_private_context_size": 10}})
        with (
            patch.object(service_module, "get_config_manager", return_value=config),
            patch.object(service_module, "can_reply", return_value=True),
            patch.object(service, "_hydrate", AsyncMock()),
            patch.object(service, "_process", process),
        ):
            self.assertEqual((await service._process_locked(self.request, None)).text, "")
        process.assert_not_awaited()

    async def test_request_lifecycle_and_cancellation(self) -> None:
        events = []
        self.register("observer", lambda event, context, details: events.append(event))
        service = service_module.ChatService(ChatSessionStore())
        config = SimpleNamespace(get_system=lambda: {"chat": {"max_private_context_size": 10}})
        with (
            patch.object(service_module, "get_config_manager", return_value=config),
            patch.object(service_module, "can_reply", return_value=True),
            patch.object(service, "_hydrate", AsyncMock()),
            patch.object(service, "_process", AsyncMock(return_value=ChatResponse(text="ok"))),
        ):
            await service._process_locked(self.request, None)
        self.assertEqual(events, ["request_start", "request_complete"])
        events.clear()
        with (
            patch.object(service_module, "get_config_manager", return_value=config),
            patch.object(service_module, "can_reply", return_value=True),
            patch.object(service, "_hydrate", AsyncMock()),
            patch.object(service, "_process", AsyncMock(side_effect=asyncio.CancelledError)),
        ):
            with self.assertRaises(asyncio.CancelledError):
                await service._process_locked(self.request, None)
        self.assertEqual(events, ["request_start", "request_cancelled"])

    def test_reply_filters_apply_in_order_and_can_suppress(self) -> None:
        self.register("reply_text", lambda context, text: text.replace("secret", "[redacted]"))
        self.register("reply_text", lambda context, text: text + "!", "later")
        self.assertEqual(hooks.filter_reply_text(self.context, "secret"), "[redacted]!")
        self.register("reply_text", lambda *args: "", "suppress")
        self.assertEqual(hooks.filter_reply_text(self.context, "secret"), "")

    async def test_reply_filter_runs_before_split_and_persistence(self) -> None:
        service = service_module.ChatService(ChatSessionStore())
        self.register("reply_text", lambda context, text: text.replace("secret", "redacted"))
        config = SimpleNamespace(
            get_system=lambda: {"chat": {"max_private_context_size": 10, "persona": "test", "behavior": {}}}
        )
        shaped = []

        async def shape(response, **kwargs):
            shaped.append(response.content)
            return response, [SimpleNamespace(text=response.content)], response.content

        with (
            patch.object(service_module, "get_config_manager", return_value=config),
            patch.object(service_module, "get_bot_group_role", AsyncMock(return_value=None)),
            patch.object(service_module, "_memory_block", AsyncMock(return_value="")),
            patch.object(service_module, "_learning_reference", AsyncMock(return_value="")),
            patch.object(service_module.prompt, "build_persona_text", return_value=("test", "persona")),
            patch.object(service_module.generator, "allowed_tools", AsyncMock(return_value=(set(), []))),
            patch.object(service_module.generator, "generate", AsyncMock(return_value=LLMResponse(content="secret"))),
            patch.object(service_module.generator, "shape_reply", shape),
            patch.object(service, "_persist_turn_start", AsyncMock(return_value=None)),
            patch.object(service, "_persist_turn_end", AsyncMock(return_value=None)) as persist,
        ):
            response = await service._process_with_provider(
                self.request, ToolRuntime(), SimpleNamespace(), focus_instruction="reply direction"
            )
        self.assertEqual(shaped, ["redacted"])
        self.assertEqual(response.text, "redacted")
        self.assertEqual(persist.await_args.args[2].content, "redacted")

    async def test_focus_shared_planner_guard_blocks_provider(self) -> None:
        from src.plugins.nonebot_plugin_fxbot.chat.focus import group_runtime

        self.register("request_guard", lambda *args: False)
        group = group_runtime.FocusGroupRuntime("hook-test")
        group.members = {self.request.session_id: SimpleNamespace(request=self.request)}
        with patch.object(group_runtime, "fallback_provider", Mock()) as provider:
            result = await group._chat([], [])
        self.assertEqual(result[0].content, "")
        provider.assert_not_called()

    async def test_clear_notifications_run_when_storage_disabled(self) -> None:
        events = []
        self.register("observer", lambda event, context, details: events.append((event, context.session_id)))
        service = service_module.ChatService(ChatSessionStore())
        with patch.object(service_module.memory_state, "is_ready", return_value=False):
            await service.clear_session(self.request.session_id)
        self.assertEqual([item[0] for item in events], ["session_clear_start", "session_clear_complete"])
        self.assertTrue(all(item[1] == self.request.session_id for item in events))

    async def test_tool_guard_is_rechecked_with_arguments(self) -> None:
        handler = Mock(return_value="done")
        registry = ToolRegistry()
        registry.register(ToolSpec("test", "test", {}, handler))
        self.register("tool_guard", lambda context, details: False if context.phase == "tool" else None)
        result = await executor.execute_tool(
            "test",
            {"path": "x"},
            ToolContext(session_id=self.context.session_id),
            ToolRuntime(),
            allowed_names={"test"},
            registry=registry,
        )
        self.assertFalse(result["ok"])
        handler.assert_not_called()

    async def test_tool_search_respects_guard_even_with_stale_allowed_names(self) -> None:
        self.register("tool_guard", lambda *args: False)
        registry = ToolRegistry()
        registry.register(ToolSpec("hidden", "secret", {}, Mock(), deferred=True))
        with patch.object(search_module, "default_registry", registry):
            result = await search_module.tool_search_tool(
                ToolContext(session_id=self.context.session_id, metadata={"allowed_tools": ["hidden"]}),
                ToolRuntime(),
                query="hidden",
            )
        self.assertNotIn("- hidden", result)

    async def test_hook_cannot_bypass_original_tool_permission(self) -> None:
        handler = Mock(return_value="done")
        spec = ToolSpec("privileged", "test", {}, handler, permission="test:admin")
        self.register("tool_guard", lambda *args: True)
        self.assertFalse(await executor.tool_allowed(spec, ToolRuntime(), session_id=self.context.session_id))

    async def test_tool_notifications_and_argument_snapshot(self) -> None:
        events = []
        self.register("observer", lambda event, context, details: events.append(event))
        registry = ToolRegistry()
        registry.register(ToolSpec("test", "test", {}, lambda context, runtime, value: value))
        result = await executor.execute_tool(
            "test",
            {"value": "ok"},
            ToolContext(session_id=self.context.session_id),
            ToolRuntime(),
            allowed_names={"test"},
            registry=registry,
        )
        self.assertEqual(result, {"ok": True, "result": "ok"})
        self.assertEqual(events, ["tool_start", "tool_complete"])

    async def test_send_guard_and_partial_delivery_notifications(self) -> None:
        events = []
        self.register("send_guard", lambda context, details: details.get("part_index", 0) == 0)
        self.register("observer", lambda event, context, details: events.append((event, details)))
        service = SimpleNamespace(
            session_store=ChatSessionStore(),
            is_current=lambda *args: True,
            is_latest=lambda *args: True,
            mark_sending=AsyncMock(),
            mark_sent=AsyncMock(),
            mark_send_aborted=AsyncMock(),
        )
        matcher = SimpleNamespace(send=AsyncMock())
        response = ChatResponse(text="ab", metadata={"reply_parts": ["a", "b"]})
        with (
            patch.object(sender, "typing_time", return_value=0),
            patch.object(sender, "apply_typo", side_effect=lambda text: SimpleNamespace(text=text, correction="")),
            patch.object(sender, "send_voice", AsyncMock()),
            patch.object(sender, "send_emoji", AsyncMock()),
            patch.object(sender, "history_enabled", return_value=False),
        ):
            text = await sender.send_parts(service, self.request, response, None, matcher)
        self.assertEqual(text, "a")
        matcher.send.assert_awaited_once()
        self.assertEqual([event for event, _ in events], ["part_sent", "delivery_complete"])
        self.assertEqual(events[-1][1]["sent_parts"], 1)
        self.assertTrue(events[-1][1]["cancelled"])

    async def test_send_guard_denies_voice_before_synthesis(self) -> None:
        self.register("send_guard", lambda *args: False)
        with (
            patch.object(sender, "tts_enabled", return_value=True),
            patch.object(sender, "synthesize", AsyncMock()) as synth,
        ):
            self.assertFalse(await sender.send_voice(None, SimpleNamespace(), "secret", context=self.context))
        synth.assert_not_awaited()

    async def test_send_guard_denies_standalone_emoji(self) -> None:
        self.register("send_guard", lambda *args: False)
        service = SimpleNamespace(is_current=lambda *args: True, is_latest=lambda *args: True)
        matcher = SimpleNamespace(send=AsyncMock())
        with patch.object(sender, "can_reply", return_value=True):
            sent = await sender.deliver_emoji(
                service,
                self.request,
                ChatResponse(text="[表情]"),
                SimpleNamespace(bot=object()),
                matcher,
                SimpleNamespace(description="笑", path="image", hash="hash"),
                0,
            )
        self.assertFalse(sent)
        matcher.send.assert_not_awaited()
