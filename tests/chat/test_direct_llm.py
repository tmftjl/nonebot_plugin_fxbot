"""纯人格直聊的模型边界、分流和真实发送回归。"""

from __future__ import annotations

import asyncio
import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock, AsyncMock, patch

from src.plugins.nonebot_plugin_fxbot.chat import hooks, router, service as service_module
from src.plugins.nonebot_plugin_fxbot.chat.focus import manager as focus_module
from src.plugins.nonebot_plugin_fxbot.chat.types import ChatRequest, ChatResponse
from src.plugins.nonebot_plugin_fxbot.chat.session import ChatSessionStore
from src.plugins.nonebot_plugin_fxbot.chat.providers import LLMResponse
from src.plugins.nonebot_plugin_fxbot.chat.session_hooks import SessionPolicy, register_session_policy
from src.plugins.nonebot_plugin_fxbot.chat.message_receive import sender, history
from src.plugins.nonebot_plugin_fxbot.config.system_defaults import SYSTEM_DEFAULTS, validate_system_config


class DirectLLMTests(unittest.IsolatedAsyncioTestCase):
    """默认路径保持不变，直聊只保留显式支持直聊的插件系统段。"""

    def setUp(self) -> None:
        self.chat = deepcopy(SYSTEM_DEFAULTS["chat"])
        self.direct = {"on": True}
        self.addCleanup(
            register_session_policy(
                "direct-test", lambda session_id: SessionPolicy(direct_reply=True) if self.direct["on"] else None
            )
        )
        self.manager = SimpleNamespace(get_system=lambda: {"chat": self.chat})
        self.service = service_module.ChatService(ChatSessionStore())
        self.request = ChatRequest("direct:bot:private:u", "u", None, "当前消息\n第二行", "t1", "m1", "private")
        self.provider = SimpleNamespace(
            provider_id="reply", chat=AsyncMock(return_value=LLMResponse(content="（原文）\n@u！"))
        )
        for module in (service_module, focus_module):
            self.enterContext(patch.object(module, "get_config_manager", return_value=self.manager))
        self.enterContext(patch.object(service_module, "can_reply", return_value=True))
        self.enterContext(patch.object(hooks, "_hooks", {}))
        self.enterContext(patch.object(self.service, "_hydrate", AsyncMock()))
        self.start = self.enterContext(
            patch.object(self.service, "_persist_turn_start", AsyncMock(return_value=(10, 11)))
        )
        self.end = self.enterContext(patch.object(self.service, "_persist_turn_end", AsyncMock(return_value=[12])))
        self.enterContext(patch.object(service_module, "get_persona_text", return_value="# 人格特征\n原始人格"))
        self.name = self.enterContext(patch.object(service_module, "effective_persona_name", return_value="会话人格"))

        async def process(request, runtime, *, focus_instruction=None):
            return await self.service._process_with_provider(
                request, runtime, self.provider, focus_instruction=focus_instruction
            )

        self.enterContext(patch.object(self.service, "_process", side_effect=process))

    async def test_only_raw_persona_visible_history_and_current_message_reach_llm(self) -> None:
        self.service.session_store.append_many(
            self.request.session_id,
            [
                {"role": "user", "content": "我叫小明", "_sender_id": "u"},
                {"role": "assistant", "content": "记住了", "_self_message": True},
            ],
        )
        self.request.quoted_text = "不能注入的引用"
        for kind in ("persona", "prompt", "reply_text"):
            self.addCleanup(hooks.register_chat_hook(kind, "must-not-run", Mock(side_effect=AssertionError(kind))))
        with (
            patch.object(service_module.prompt, "build_persona_text", side_effect=AssertionError("prompt")),
            patch.object(service_module.conversation, "run", side_effect=AssertionError("planner")),
            patch.object(service_module.generator, "allowed_tools", side_effect=AssertionError("tools")),
            patch.object(service_module.generator, "shape_reply", side_effect=AssertionError("splitter")),
            patch.object(service_module, "_describe_images", side_effect=AssertionError("vision")),
            patch.object(service_module, "_memory_block", side_effect=AssertionError("memory")),
        ):
            response = await self.service.process(self.request)
        self.provider.chat.assert_awaited_once_with(
            messages=[
                {"role": "system", "content": "# 人格特征\n原始人格"},
                {"role": "user", "content": "我叫小明"},
                {"role": "assistant", "content": "记住了"},
                {"role": "user", "content": self.request.text},
            ],
            tools=None,
            temperature=1.0,
            max_tokens=8192,
        )
        self.assertEqual(response.text, "（原文）\n@u！")
        self.assertEqual(
            response.metadata,
            {"direct_llm": True, "reply_parts": [response.text], "turn_item_ids": [12], "turn_db_id": 10},
        )
        self.name.assert_called_once_with(self.request.session_id, self.chat["persona"])
        self.assertNotIn("planner_loop", self.request.metadata)
        self.assertEqual(self.end.await_args.args[6], [response.text])

    async def test_infinite_gen2_toggle_controls_complete_direct_system_prompt(self) -> None:
        from src.plugins.nonebot_plugin_fxbot.plugins.infinite_gen2 import integration

        integration.install()
        integration.install()
        self.service.session_store.append_many(
            self.request.session_id,
            [
                {"role": "user", "content": "上条问题", "_sender_id": "u"},
                {"role": "assistant", "content": "上条已发回复", "_self_message": True},
            ],
        )
        for enabled in (False, True, False, True):
            with self.subTest(enabled=enabled), patch.object(integration, "is_enabled", return_value=enabled):
                await self.service.process(self.request)
                messages = self.provider.chat.await_args.kwargs["messages"]
                expected_system = "# 人格特征\n原始人格"
                if enabled:
                    expected_system += "\n\n" + integration.PROMPT_TEXT
                self.assertEqual(
                    messages,
                    [
                        {"role": "system", "content": expected_system},
                        {"role": "user", "content": "上条问题"},
                        {"role": "assistant", "content": "上条已发回复"},
                        {"role": "user", "content": self.request.text},
                    ],
                )
                self.assertEqual(messages[0]["content"].count(integration.PROMPT_TEXT), int(enabled))
                self.assertFalse(any(integration.PROMPT_TEXT in str(item) for item in messages[1:]))
                self.assertIsNone(self.provider.chat.await_args.kwargs["tools"])

    async def test_direct_system_sections_have_explicit_phase_identity_and_order(self) -> None:
        contexts = []

        def contribute(context):
            contexts.append(context)
            if context.phase == "direct_reply":
                return hooks.SystemPromptSection("直聊段", order=100)
            return None

        self.addCleanup(hooks.register_chat_hook("system_prompt", "direct", contribute))
        self.addCleanup(
            hooks.register_chat_hook(
                "system_prompt",
                "reply-only",
                lambda context: hooks.SystemPromptSection("普通回复段") if context.phase == "reply" else None,
            )
        )
        self.addCleanup(
            hooks.register_chat_hook(
                "system_prompt",
                "earlier",
                lambda context: (
                    hooks.SystemPromptSection("较早直聊段", order=0) if context.phase == "direct_reply" else None
                ),
            )
        )
        await self.service.process(self.request)
        messages = self.provider.chat.await_args.kwargs["messages"]
        self.assertEqual(messages[0], {"role": "system", "content": "# 人格特征\n原始人格\n\n较早直聊段\n\n直聊段"})
        self.assertEqual(len(contexts), 1)
        self.assertEqual(contexts[0].phase, "direct_reply")
        self.assertEqual(contexts[0].session_id, self.request.session_id)
        self.assertEqual(contexts[0].user_id, self.request.user_id)
        self.assertEqual(contexts[0].turn_id, self.request.turn_id)

    async def test_direct_system_section_error_stops_provider_call(self) -> None:
        self.addCleanup(hooks.register_chat_hook("system_prompt", "broken", lambda context: "invalid"))
        with patch.object(self.service, "_mark_turn_failed", AsyncMock()), self.assertRaises(hooks.ChatHookError):
            await self.service.process(self.request)
        self.provider.chat.assert_not_awaited()

    async def test_history_excludes_internal_unsent_and_other_session_messages(self) -> None:
        store = self.service.session_store
        store.append_many(
            self.request.session_id,
            [
                {"role": "user", "content": "真实消息", "_sender_id": "u", "_source_ids": ["old"]},
                {"role": "system", "content": "旧系统提示"},
                {"role": "user", "content": "规划指令", "_sender_id": "u", "_planner_internal": True},
                {"role": "user", "content": "参考资料", "_sender_id": "u", "_reference": True},
                {"role": "assistant", "content": "工具调用", "_self_message": True, "tool_calls": [{"id": "call"}]},
                {"role": "tool", "content": "工具结果", "tool_call_id": "call"},
                {"role": "assistant", "content": "未发送的生成内容"},
                {"role": "assistant", "content": "已发送正文", "_self_message": True, "_logical_turn_id": "old"},
            ],
        )
        store.append("other-session", {"role": "user", "content": "其他群的消息", "_sender_id": "u"})
        await self.service.process(self.request)
        messages = self.provider.chat.await_args.kwargs["messages"]
        self.assertEqual(
            messages[1:-1],
            [
                {"role": "user", "content": "真实消息"},
                {"role": "assistant", "content": "已发送正文"},
            ],
        )
        self.assertTrue(all(set(message) == {"role", "content"} for message in messages))

    async def test_history_limits_follow_private_group_and_zero_settings(self) -> None:
        self.chat["max_private_context_size"] = 2
        self.chat["max_context_size"] = 1
        for group_id, limit in ((None, 2), ("group", 1), (None, 0)):
            with self.subTest(group_id=group_id, limit=limit):
                self.request.group_id = group_id
                if not limit:
                    self.chat["max_private_context_size"] = 0
                self.service.session_store.append_many(
                    self.request.session_id,
                    [{"role": "user", "content": str(index), "_sender_id": "u"} for index in range(5)],
                )
                await self.service.process(self.request)
                messages = self.provider.chat.await_args.kwargs["messages"]
                self.assertEqual(
                    messages[1:-1], [{"role": "user", "content": str(index)} for index in range(5 - limit, 5)]
                )

    async def test_current_message_is_not_replayed_twice(self) -> None:
        self.request.message_id = "current"
        self.request.metadata["history_recorded"] = True
        self.service.session_store.append_many(
            self.request.session_id,
            [
                {"role": "user", "content": "上条消息", "_sender_id": "u", "_source_ids": ["old"]},
                {"role": "user", "content": self.request.text, "_sender_id": "u", "_source_ids": ["current"]},
            ],
        )
        await self.service.process(self.request)
        messages = self.provider.chat.await_args.kwargs["messages"]
        self.assertEqual(messages[1:-1], [{"role": "user", "content": "上条消息"}])
        self.assertEqual(sum(item["content"] == self.request.text for item in messages), 1)

    async def test_previous_sent_reply_is_used_by_next_turn(self) -> None:
        matcher = SimpleNamespace(send=AsyncMock(return_value={"message_id": "sent1"}))
        runtime = SimpleNamespace(matcher=matcher, bot=None)
        with (
            patch.object(sender, "can_reply", return_value=True),
            patch.object(history.memory_state, "is_ready", return_value=False),
        ):
            response = await self.service.process(self.request, runtime)
            self.assertTrue(await self.service._send_generated(self.request, response, runtime))
            next_request = ChatRequest(self.request.session_id, "u", None, "我上一句说了什么？", "t2", "m2", "private")
            await self.service.process(next_request, runtime)
        self.assertEqual(
            self.provider.chat.await_args.kwargs["messages"][1:-1],
            [
                {"role": "user", "content": self.request.text},
                {"role": "assistant", "content": response.text},
            ],
        )

    async def test_images_go_to_reply_provider_without_vision_call(self) -> None:
        self.request.images = ["data:image/png;base64,abc"]
        await self.service.process(self.request)
        self.assertEqual(
            self.provider.chat.await_args.kwargs["messages"][1]["content"],
            [
                {"type": "text", "text": self.request.text},
                {"type": "image_url", "image_url": {"url": self.request.images[0]}},
            ],
        )

    async def test_request_guard_still_denies_before_provider(self) -> None:
        guard = Mock(return_value=False)
        self.addCleanup(hooks.register_chat_hook("request_guard", "deny", guard))
        self.assertEqual((await self.service.process(self.request)).text, "")
        self.provider.chat.assert_not_awaited()
        self.assertEqual(guard.call_args.args[0].phase, "reply")

    async def test_proactive_focus_and_old_batches_do_not_generate(self) -> None:
        for kwargs in ({"proactive": True}, {"batched": True}, {"focus": True}):
            with self.subTest(kwargs=kwargs):
                self.request.proactive = kwargs.get("proactive", False)
                self.request.batched = kwargs.get("batched", False)
                result = await self.service._process_locked(
                    self.request, None, focus_instruction="旧 Focus 指令" if kwargs.get("focus") else None
                )
                self.assertEqual(result.text, "")
        self.provider.chat.assert_not_awaited()

    async def test_clear_during_generation_discards_result(self) -> None:
        async def cleared(**kwargs):
            self.service.session_store.set_epoch(self.request.session_id, 1)
            return LLMResponse(content="过期正文")

        self.provider.chat.side_effect = cleared
        with patch.object(self.service, "_mark_turn_aborted", AsyncMock()) as aborted:
            self.assertEqual((await self.service.process(self.request)).text, "")
        aborted.assert_awaited_once()
        self.end.assert_not_awaited()

    async def test_new_trigger_during_generation_discards_old_result(self) -> None:
        store = self.service.session_store
        self.request.trigger_seq = store.next_seq(self.request.session_id, "u")

        async def newer(**kwargs):
            store.next_seq(self.request.session_id, "u")
            return LLMResponse(content="旧正文")

        self.provider.chat.side_effect = newer
        with patch.object(self.service, "_mark_turn_aborted", AsyncMock()):
            self.assertEqual((await self.service.process(self.request)).text, "")
        self.end.assert_not_awaited()

    async def test_mode_is_snapshotted_before_model_and_send(self) -> None:
        async def reload(**kwargs):
            self.direct["on"] = False
            return LLMResponse(content="保持原样")

        self.provider.chat.side_effect = reload
        response = await self.service.process(self.request)
        self.assertTrue(response.metadata["direct_llm"])

    async def test_queue_does_not_feed_direct_requests_to_planner(self) -> None:
        active = deepcopy(self.request)
        self.service._active_requests[self.request.session_id] = active
        self.assertFalse(await self.service.queue_message(self.request))
        self.assertNotIn("planner_pending", active.metadata)
        self.direct["on"] = False
        active.metadata["_direct_llm"] = True
        self.assertFalse(await self.service.queue_message(self.request))

    async def test_concurrent_direct_turns_are_not_lost_to_planner_queue(self) -> None:
        entered = asyncio.Event()
        release = asyncio.Event()

        async def generate(**kwargs):
            entered.set()
            await release.wait()
            return LLMResponse(content="回复")

        self.provider.chat.side_effect = generate
        second = deepcopy(self.request)
        second.turn_id, second.dedupe_key, second.text = "t2", "m2", "第二条"
        with patch.object(self.service, "_send_generated", AsyncMock(return_value=True)):
            first_task = asyncio.create_task(self.service.process_and_send(self.request))
            await asyncio.wait_for(entered.wait(), 2)
            second_task = asyncio.create_task(self.service.process_and_send(second))
            await asyncio.sleep(0)
            release.set()
            self.assertEqual(await asyncio.gather(first_task, second_task), [True, True])
        self.assertEqual(self.provider.chat.await_count, 2)
        self.assertEqual(self.service._active_requests, {})

    async def test_focus_is_disabled_and_hot_reload_restores_it(self) -> None:
        self.chat["focus"].update(enabled=True, on_private=True)
        manager = focus_module.FocusModeManager()
        self.assertFalse(manager.is_enabled_for_request(self.request))
        self.direct["on"] = False
        self.assertTrue(manager.is_enabled_for_request(self.request))

    async def test_disabled_mode_uses_existing_provider_path(self) -> None:
        self.direct["on"] = False
        with patch.object(
            self.service, "_process_with_provider", AsyncMock(return_value=ChatResponse("正常"))
        ) as normal:
            self.assertEqual((await self.service.process(self.request)).text, "正常")
        self.assertFalse(self.request.metadata["_direct_llm"])
        self.assertIsNone(normal.await_args.kwargs["focus_instruction"])

    async def test_visible_history_only_appends_after_actual_send(self) -> None:
        matcher = SimpleNamespace(send=AsyncMock(return_value={"message_id": "sent1"}))
        runtime = SimpleNamespace(matcher=matcher, bot=None)
        with (
            patch.object(sender, "can_reply", return_value=True),
            patch.object(history.memory_state, "is_ready", return_value=False),
        ):
            response = await self.service.process(self.request, runtime)
            before = self.service.session_store.get(self.request.session_id)
            self.assertFalse(any(item["role"] == "assistant" for item in before))
            self.assertTrue(await self.service._send_generated(self.request, response, runtime))
            after = self.service.session_store.get(self.request.session_id)
        assistant = [item for item in after if item["role"] == "assistant"]
        self.assertEqual(len(assistant), 1)
        self.assertEqual(assistant[0]["content"], response.text)
        matcher.send.assert_awaited_once_with(response.text)

    async def test_full_provider_path_records_direct_reply_trace(self) -> None:
        original = service_module.ChatService._process.__get__(self.service)
        self.provider.name = "reply"
        with (
            patch.object(self.service, "_process", original),
            patch.object(service_module, "candidate_providers", return_value=["reply"]) as candidates,
            patch.object(service_module, "TaskProvider") as scope,
            patch.object(history, "record_trace", AsyncMock()) as trace,
        ):
            scope.return_value.__aenter__.return_value = self.provider
            response = await self.service.process(self.request)
        self.assertTrue(response.metadata["direct_llm"])
        candidates.assert_called_once_with("reply", self.request.session_id)
        self.provider.chat.assert_awaited_once()
        trace.assert_awaited_once()


class DirectSendTests(unittest.IsolatedAsyncioTestCase):
    """只发送一条原始正文，仍保留守卫和实际发送记录。"""

    async def test_raw_single_message_and_send_guard(self) -> None:
        request = ChatRequest("direct", "u", None, "问题", "t", "m", "private")
        text = "（表情）@u：原样。\n第二行！"
        response = ChatResponse(text, metadata={"direct_llm": True, "reply_parts": [text]})
        service = SimpleNamespace(
            session_store=ChatSessionStore(),
            is_current=lambda *args: True,
            is_latest=lambda *args: True,
            mark_sending=AsyncMock(),
            mark_sent=AsyncMock(),
            mark_send_aborted=AsyncMock(),
            mark_send_failed=AsyncMock(),
        )
        for allowed in (True, False):
            with (
                self.subTest(allowed=allowed),
                patch.object(sender, "can_reply", return_value=True),
                patch.object(sender, "guard", AsyncMock(return_value=allowed)) as guard,
                patch.object(sender, "apply_typo", side_effect=AssertionError("typo")),
                patch.object(sender, "split_mentions", side_effect=AssertionError("mention")),
                patch.object(sender, "first_payload", side_effect=AssertionError("quote")),
                patch.object(sender, "send_voice", side_effect=AssertionError("voice")),
                patch.object(sender, "send_emoji", side_effect=AssertionError("emoji")),
                patch.object(sender, "send_pictures", side_effect=AssertionError("pictures")),
                patch.object(sender.chat_writeback, "on_reply", side_effect=AssertionError("writeback")),
            ):
                matcher = SimpleNamespace(send=AsyncMock(return_value={"message_id": "sent1"}))
                result = await sender._send_parts(service, request, response, None, matcher, {"sent_parts": 0})
                self.assertEqual(result, text if allowed else "")
                guard.assert_awaited_once()
                self.assertEqual(matcher.send.await_count, int(allowed))
                if allowed:
                    matcher.send.assert_awaited_once_with(text)
                    self.assertEqual(service.mark_sent.await_args.kwargs["content"], text)


class DirectConfigTests(unittest.TestCase):
    """开关由无限二代插件提供，核心只保留会话策略。"""

    def test_defaults_and_validation(self) -> None:
        from src.plugins.nonebot_plugin_fxbot.plugins.infinite_gen2.config import validate_config
        from src.plugins.nonebot_plugin_fxbot.plugins.infinite_gen2.ui_schema import DEFAULTS

        self.assertNotIn("direct_llm", SYSTEM_DEFAULTS["chat"])
        validate_system_config({"chat": {}})
        self.assertIs(DEFAULTS["direct_llm"], False)
        for value in (True, False):
            validate_config({"direct_llm": value})
        for value in ("true", 1, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_config({"direct_llm": value})

    def test_console_exposes_switch_in_plugin_card(self) -> None:
        from src.plugins.nonebot_plugin_fxbot.console.routes.config import _chat_config_tab, _plugin_config_tabs

        chat_card = next(card for card in _chat_config_tab()["cards"] if card["key"] == "chat")
        self.assertFalse(any(field["field"] == "direct_llm" for field in chat_card["schemas"]))
        card = next(card for tab in _plugin_config_tabs() for card in tab["cards"] if card["key"] == "infinite_gen2")
        field = next(field for field in card["schemas"] if field["field"] == "direct_llm")
        self.assertEqual(field["component"], "Switch")
        self.assertIs(field["default"], False)


class DirectPersistenceTests(unittest.IsolatedAsyncioTestCase):
    """直聊只存原始对话，不派发图片记忆与摘要任务。"""

    async def test_no_image_memory_or_writeback_at_turn_start(self) -> None:
        request = ChatRequest("direct", "u", None, "问题", "t", "m", "private", metadata={"_direct_llm": True})
        with (
            patch.object(history.memory_state, "is_ready", return_value=True),
            patch.object(history, "history_enabled", return_value=True),
            patch.object(history.chat_repository, "upsert_turn", AsyncMock(return_value=1)),
            patch.object(history.chat_repository, "append_message", AsyncMock(return_value=2)),
            patch.object(history.chat_repository, "append_turn_item", AsyncMock(return_value=3)),
            patch.object(history, "_track_message", side_effect=AssertionError("writeback")),
        ):
            self.assertEqual(await history.persist_turn_start(request, request.text, 0), (1, 2))


class DirectRouterTests(unittest.IsolatedAsyncioTestCase):
    """触发直达服务，旁观消息不启动识图、学习或主动规划。"""

    async def test_trigger_bypasses_scheduler_and_context_recording(self) -> None:
        service = service_module.ChatService(ChatSessionStore())
        request = ChatRequest("direct-route", "u", None, "你好", "t", "m", "private")
        identity = SimpleNamespace(session_id=request.session_id, dedupe_key="m", message_id="m")
        event = SimpleNamespace()
        runtime = object()
        self.addCleanup(register_session_policy("direct-route", lambda session_id: SessionPolicy(direct_reply=True)))
        with (
            patch.object(router, "event_identity", return_value=identity),
            patch.object(router, "event_user_id", return_value="u"),
            patch.object(router, "event_group_id", return_value=None),
            patch.object(router, "chat_service", service),
            patch.object(service, "ensure_hydrated", AsyncMock()),
            patch.object(service, "process_and_send", AsyncMock(return_value=True)) as direct,
            patch.object(service, "record_group_message", side_effect=AssertionError("history/vision")),
            patch.object(router, "adapt_message_event", AsyncMock(return_value=request)) as adapt,
            patch.object(router, "_focus_or_detach", AsyncMock(return_value=False)),
            patch.object(router.default_runtime_factory, "create", return_value=runtime),
            patch.object(router.normal_scheduler, "submit", side_effect=AssertionError("scheduler")),
            patch.object(router.emoji_module, "collect_in_background", side_effect=AssertionError("emoji")),
        ):
            await router._handle_ai_fallback(object(), event, object())
            direct.assert_awaited_once_with(request, runtime)
            self.assertFalse(adapt.await_args.kwargs["include_quote"])
            await router._handle_ai_fallback(object(), event, object())
            self.assertEqual(direct.await_count, 1)
            await router._handle_context(object(), event)
            self.assertFalse(await router._proactive_gate(object(), event, text="普通群消息", has_image=False))
