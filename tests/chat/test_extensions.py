"""统一扩展注册、阶段拒绝、学习接线及后台续话的安全回归。"""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from dataclasses import replace
from unittest.mock import Mock, AsyncMock, patch

from src.plugins.nonebot_plugin_fxbot.chat import hooks, access, extensions, continuation, session_hooks
from src.plugins.nonebot_plugin_fxbot.chat.tools import ToolRuntime
from src.plugins.nonebot_plugin_fxbot.chat.types import ChatRequest
from src.plugins.nonebot_plugin_fxbot.chat.memory import expression, write_hooks
from src.plugins.nonebot_plugin_fxbot.chat.stages import execute_stage
from src.plugins.nonebot_plugin_fxbot.chat.replyer import generator
from src.plugins.nonebot_plugin_fxbot.chat.session import ChatSessionStore
from src.plugins.nonebot_plugin_fxbot.chat.providers import LLMResponse


class ExtensionTests(unittest.IsolatedAsyncioTestCase):
    """所有 Hook 类型共用注册顺序、替换和注销语义。"""

    def setUp(self) -> None:
        self.enterContext(patch.object(hooks, "_hooks", {}))
        self.enterContext(patch.object(hooks, "logger", Mock()))
        self.enterContext(patch.object(session_hooks, "_policies", {}))
        self.context = hooks.ChatHookContext("test:bot:private:user", user_id="user", turn_id="turn", epoch=0)

    def extension(self, name="test"):
        extension = extensions.ChatExtension(name)
        self.addCleanup(extension.close)
        return extension

    async def test_priority_is_stable_and_same_callback_has_new_registration_identity(self) -> None:
        calls = []

        def callback(*args):
            calls.append("same")

        old = hooks.register_chat_hook("observer", "same", callback)
        remove = hooks.register_chat_hook("observer", "same", callback)
        self.addCleanup(remove)
        old()
        extension = self.extension()
        extension.on("observer", lambda *args: calls.append("early"), priority=0)
        await hooks.notify("event", self.context)
        self.assertEqual(calls, ["early", "same"])

    def test_session_hooks_use_unified_entry_and_close(self) -> None:
        extension = self.extension()
        extension.on("session_policy", lambda sid: extensions.SessionPolicy(allow_sharing=False))
        self.assertFalse(session_hooks.session_policy(self.context.session_id).allow_sharing)
        extension.close()
        extension.close()
        self.assertTrue(session_hooks.session_policy(self.context.session_id).allow_sharing)
        with self.assertRaises(RuntimeError):
            extension.on("prompt", lambda context: "extra")

    def test_reload_old_group_does_not_remove_new_hooks(self) -> None:
        old, current = self.extension(), self.extension()
        old.on("reply_text", lambda context, text: "old")
        current.on("reply_text", lambda context, text: "new")
        old.close()
        self.assertEqual(hooks.filter_reply_text(self.context, "input"), "new")
        self.assertNotEqual(old._task_owner, current._task_owner)

    def test_async_callable_filters_rejected(self) -> None:
        class Callback:
            async def __call__(self, *args):
                return None

        with self.assertRaises(ValueError):
            self.extension().on("reply_text", Callback())
        with self.assertRaises(ValueError):
            self.extension().on("session_policy", Callback())

    async def test_stage_denied_never_creates_operation(self) -> None:
        events = []
        extension = self.extension()
        extension.on("stage_guard", lambda context, details: False)
        extension.on("observer", lambda event, context, details: events.append((event, details["stage"])))
        operation = AsyncMock(return_value=True)
        self.assertFalse(await execute_stage("learning_write", self.context, operation, rejected=False))
        operation.assert_not_called()
        self.assertEqual(events, [("stage_rejected", "learning_write")])

    async def test_stage_reserved_details_fail_before_any_operation_or_notification(self) -> None:
        operation = AsyncMock(return_value=True)
        observer = Mock()
        self.extension().on("observer", observer)
        for key in ("result", "event", "hook_kind"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                await execute_stage("learning_write", self.context, operation, rejected=False, **{key: "reserved"})
        operation.assert_not_called()
        observer.assert_not_called()

    async def test_stage_success_failure_and_cancellation(self) -> None:
        events = []
        self.extension().on("observer", lambda event, *args: events.append(event))
        self.assertEqual(await execute_stage("reply", self.context, AsyncMock(return_value="ok"), rejected=""), "ok")
        self.assertEqual(events, ["stage_start", "stage_complete"])
        for error, event in ((RuntimeError(), "stage_error"), (asyncio.CancelledError(), "stage_cancelled")):
            events.clear()
            with self.assertRaises(type(error)):
                await execute_stage("reply", self.context, AsyncMock(side_effect=error), rejected="")
            self.assertEqual(events, ["stage_start", event])

    async def test_learning_guard_freezes_payload_and_blocks_repository(self) -> None:
        def reject(context, details):
            self.assertEqual(details["kind"], "expression")
            with self.assertRaises(TypeError):
                details["new_items"][0]["style"] = "changed"
            return False

        self.extension().on("stage_guard", reject)
        save = AsyncMock(return_value=True)
        items = [{"style": "original"}]
        self.assertFalse(await write_hooks.write_learning("tenant", "expression", save, new_items=items))
        save.assert_not_called()
        self.assertEqual(items, [{"style": "original"}])

    async def test_empty_expression_batch_is_also_guarded(self) -> None:
        self.extension().on("stage_guard", lambda *args: False)
        messages = [SimpleNamespace(id=index) for index in range(1, 11)]
        with (
            patch.object(expression, "is_ready", return_value=True),
            patch.object(expression, "learning_flags", return_value=(True, True)),
            patch.object(expression, "tenant_learn_enabled", return_value=True),
            patch.object(expression, "_clean_messages", return_value=[]),
            patch.object(expression.chat_repository, "get_session_state", AsyncMock()),
            patch.object(expression.chat_repository, "get_cursor", AsyncMock(return_value=None)),
            patch.object(expression.chat_repository, "save_expressions", AsyncMock()) as save,
        ):
            self.assertFalse(
                await expression.learn_expressions("tenant", cfg={"self_reflect": False}, messages=messages)
            )
        save.assert_not_called()

    async def test_reply_stage_denied_never_calls_model(self) -> None:
        self.extension().on("stage_guard", lambda context, details: details["stage"] != "reply")
        request = ChatRequest(self.context.session_id, "user", None, "hi", "turn", "msg", "private")
        provider = SimpleNamespace(chat=AsyncMock(return_value=LLMResponse(content="not sent")))
        result = await generator.generate(None, request, ToolRuntime(), provider, [], None, set(), 0, 10)
        self.assertEqual(result.content, "")
        provider.chat.assert_not_called()

    async def test_expression_candidates_are_visible_before_repository_write(self) -> None:
        seen = []

        def reject(context, details):
            seen.extend(details["new_items"])
            return False

        self.extension().on("stage_guard", reject)
        messages = [SimpleNamespace(id=index) for index in range(1, 11)]
        with (
            patch.object(expression, "is_ready", return_value=True),
            patch.object(expression, "learning_flags", return_value=(True, True)),
            patch.object(expression, "tenant_learn_enabled", return_value=True),
            patch.object(expression, "_clean_messages", return_value=messages),
            patch.object(
                expression,
                "request_expressions",
                AsyncMock(return_value=[expression.ExpressionItem("情境", "候选说法", source_seq=1)]),
            ),
            patch.object(expression.chat_repository, "get_session_state", AsyncMock()),
            patch.object(expression.chat_repository, "get_cursor", AsyncMock(return_value=None)),
            patch.object(expression.chat_repository, "list_expressions", AsyncMock(return_value=[])),
            patch.object(expression.chat_repository, "save_expressions", AsyncMock()) as save,
        ):
            self.assertFalse(
                await expression.learn_expressions(
                    "tenant", provider=object(), cfg={"self_reflect": False}, messages=messages
                )
            )
        save.assert_not_called()
        self.assertEqual([item["style"] for item in seen], ["候选说法"])

    async def test_focus_planning_stage_does_not_borrow_member_identity(self) -> None:
        from src.plugins.nonebot_plugin_fxbot.chat.focus.group_runtime import FocusGroupRuntime

        seen = []

        def reject(context, details):
            seen.append(context)
            return False

        self.extension().on("stage_guard", reject)
        group = FocusGroupRuntime("scope", store=SimpleNamespace())
        with patch.object(group, "_chat_guarded", AsyncMock()) as generate:
            result, _, _ = await group._chat([], [])
        generate.assert_not_called()
        self.assertEqual(result.content, "")
        self.assertEqual(seen, [hooks.ChatHookContext("scope", phase="focus_planner")])

    async def test_shared_timeline_records_only_sent_parts_and_deduplicates(self) -> None:
        from src.plugins.nonebot_plugin_fxbot.chat.focus.manager import FocusModeManager
        from src.plugins.nonebot_plugin_fxbot.chat.focus.group_runtime import FocusGroupRuntime

        manager = FocusModeManager()
        group = FocusGroupRuntime("scope", store=SimpleNamespace())
        request = ChatRequest(self.context.session_id, "", None, "", "turn", "msg", "proactive")
        group.members[request.session_id] = SimpleNamespace()
        manager.groups["scope"] = group
        manager._scope_by_session[request.session_id] = "scope"
        with patch.object(group, "is_member_valid", return_value=True), patch.object(group, "checkpoint", AsyncMock()):
            await manager.record_extension_part(request, 0, "actual", "receipt")
            await manager.record_extension_part(request, 0, "duplicate", "receipt")
        self.assertEqual(len(group.entries), 1)
        self.assertEqual(group.entries[0].messages[0]["content"], f"[{request.session_id}] actual")
        self.assertEqual(group.entries[0].sources, {request.session_id: ["receipt"]})
        self.assertEqual(group._generation, 1)
        self.assertEqual(group._pending, {})
        self.assertEqual(group._ready, set())

    async def test_ingress_guard_receives_only_snapshot_and_can_reject(self) -> None:
        from src.plugins.nonebot_plugin_fxbot.chat import router

        seen = []

        def reject(context, details):
            seen.append((context, details))
            return False

        self.extension().on("ingress_guard", reject)
        event = SimpleNamespace()
        with (
            patch.object(router, "session_id_for_event", return_value=self.context.session_id),
            patch.object(router, "event_user_id", return_value="user"),
            patch.object(router, "event_group_id", return_value=None),
            patch.object(router, "message_text", return_value="text"),
        ):
            self.assertFalse(await router._ingress_allowed(None, event, path="context"))
        context, details = seen[0]
        self.assertEqual(context.phase, "ingress")
        self.assertEqual(details, {"path": "context", "text": "text"})


class ContinuationTests(unittest.IsolatedAsyncioTestCase):
    """后台对话只继承真实目标，不继承用户权限或伪造消息。"""

    def setUp(self) -> None:
        self.scheduler = continuation.ReplyScheduler()
        self.addAsyncCleanup(self.scheduler.stop)
        self.enterContext(patch.object(hooks, "_hooks", {}))
        self.enterContext(patch.object(continuation, "bind_bot"))
        self.store = ChatSessionStore()
        self.bot = SimpleNamespace(self_id="bot")
        self.service = SimpleNamespace(
            session_store=self.store,
            is_current=lambda sid, epoch: self.store.epoch(sid) == epoch,
            focus_reply=AsyncMock(return_value="sent"),
        )
        self.request = ChatRequest(
            "test:bot:private:user", "user", None, "hi", "turn", "msg", "private", platform="test", accepted_epoch=0
        )
        self.runtime = ToolRuntime(bot=self.bot, event=SimpleNamespace())
        self.enterContext(patch.object(continuation, "extract_message_target", return_value={"user_id": "user"}))
        self.scheduler.remember(self.service, self.request, self.runtime)
        self.context = hooks.hook_context(self.request)
        self.eligible = self.enterContext(patch.object(continuation, "_eligible", AsyncMock(return_value=True)))

    async def test_continuation_uses_captured_identity_and_has_no_user_event(self) -> None:
        forged = replace(self.context, group_id="other", platform="other")
        task = self.scheduler.schedule("plugin", "reply", forged, "continue")
        self.assertEqual(await task, "sent")
        args = self.service.focus_reply.await_args
        request, runtime, instruction = args.args
        self.assertIsNone(request.group_id)
        self.assertEqual(request.platform, "test")
        self.assertEqual(request.user_id, "")
        self.assertEqual(request.text, "")
        self.assertTrue(request.proactive)
        self.assertTrue(request.metadata["continuation"])
        self.assertIsNone(runtime.event)
        self.assertEqual(runtime.allowed_tools, frozenset())
        self.assertEqual(instruction, "continue")
        self.assertEqual(self.scheduler.tasks, {})

    async def test_continuation_runtime_rejects_file_tool_before_any_filesystem_write(self) -> None:
        from src.plugins.nonebot_plugin_fxbot.chat.tools import (
            ToolContext,
            file as file_tools,
            executor,
            default_registry,
        )

        await self.scheduler.schedule("plugin", "reply", self.context, "continue")
        runtime = self.service.focus_reply.await_args.args[1]
        spec = default_registry.get("send_file")
        self.assertIsNotNone(spec)
        self.assertFalse(await executor.tool_allowed(spec, runtime, session_id=self.context.session_id))
        with patch.object(file_tools, "cache_dir", side_effect=AssertionError("不得创建缓存路径")) as cache_path:
            result = await executor.execute_tool(
                "send_file",
                {"name": "not-created.md", "content": "text"},
                ToolContext(session_id=self.context.session_id),
                runtime,
                allowed_names={"send_file"},
            )
        self.assertFalse(result["ok"])
        cache_path.assert_not_called()

    async def test_new_message_or_clear_prevents_generation(self) -> None:
        task = self.scheduler.schedule("plugin", "reply", self.context, "continue")
        self.store.mark_seen(self.context.session_id, "new-message")
        self.assertEqual(await task, "")
        self.service.focus_reply.assert_not_called()
        task = self.scheduler.schedule("plugin", "reply", self.context, "continue")
        self.store.clear(self.context.session_id)
        self.assertEqual(await task, "")
        self.service.focus_reply.assert_not_called()

    async def test_offline_or_restricted_prevents_generation(self) -> None:
        self.eligible.return_value = False
        self.assertEqual(await self.scheduler.schedule("plugin", "reply", self.context, "continue"), "")
        self.service.focus_reply.assert_not_called()

    async def test_replace_cancel_clear_and_shutdown(self) -> None:
        first = self.scheduler.schedule("plugin", "reply", self.context, "first", delay=60)
        second = self.scheduler.schedule("plugin", "reply", self.context, "second", delay=60)
        await asyncio.sleep(0)
        self.assertTrue(first.cancelled())
        self.scheduler.cancel(session_id=self.context.session_id)
        with self.assertRaises(asyncio.CancelledError):
            await second
        self.assertEqual(self.scheduler.bindings, {})
        await self.scheduler.stop()
        with self.assertRaises(RuntimeError):
            self.scheduler.schedule("plugin", "reply", self.context, "continue")

    async def test_clear_during_stage_guard_prevents_generation(self) -> None:
        def clear(context, details):
            self.scheduler.cancel(session_id=context.session_id)

        remove = hooks.register_chat_hook("stage_guard", "clear", clear)
        self.addCleanup(remove)
        self.assertEqual(await self.scheduler.schedule("plugin", "reply", self.context, "continue"), "")
        self.service.focus_reply.assert_not_called()

    async def test_owner_unload_cancels_only_its_tasks(self) -> None:
        first = self.scheduler.schedule("old-instance", "reply", self.context, "first", delay=60)
        second = self.scheduler.schedule("new-instance", "reply", self.context, "second", delay=60)
        self.scheduler.cancel(owner="old-instance")
        with self.assertRaises(asyncio.CancelledError):
            await first
        self.assertFalse(second.cancelled())

    async def test_unknown_context_and_invalid_delay_rejected(self) -> None:
        for context in (replace(self.context, turn_id="unknown"), replace(self.context, session_id="other")):
            with self.assertRaises(ValueError):
                self.scheduler.schedule("plugin", "reply", context, "continue")
        for delay in (-1, float("nan"), float("inf"), True, 3601):
            with self.assertRaises(ValueError):
                self.scheduler.schedule("plugin", "reply", self.context, "continue", delay=delay)

    async def test_sending_rechecks_eligibility_and_actual_platform_target(self) -> None:
        async def reply(request, runtime, instruction, *, is_valid):
            await runtime.matcher.send("payload")
            self.eligible.return_value = False
            with self.assertRaises(RuntimeError):
                await runtime.matcher.send("blocked")
            return "payload"

        self.service.focus_reply.side_effect = reply
        with (
            patch.object(continuation, "build_message_segment", return_value="segment"),
            patch.object(continuation, "build_message", return_value="message"),
            patch.object(continuation, "send_message_to_target", AsyncMock()) as send,
        ):
            self.assertEqual(await self.scheduler.schedule("plugin", "reply", self.context, "continue"), "payload")
        send.assert_awaited_once_with(self.bot, {"user_id": "user"}, "message")

    async def test_membership_recheck_does_not_use_original_admin(self) -> None:
        binding = replace(self.scheduler.bindings[self.context.session_id], context=replace(self.context, group_id="g"))
        from src.plugins.nonebot_plugin_fxbot.membership.guard import membership_guard

        # 直接使用保存的真实实现，避免测试替身掩盖门禁调用参数。
        config = SimpleNamespace(
            get_system=lambda: {
                "membership": {"enabled": True, "free_bot_ids": []},
                "chat": {"group_allowlist": []},
            }
        )
        with (
            patch.object(access, "get_bots", return_value={"bot": self.bot}),
            patch.object(access, "can_reply", return_value=True),
            patch.object(access, "direct_reply_mode", return_value=False),
            patch.object(access, "proactive_switch", AsyncMock(return_value=None)),
            patch.object(access, "get_manager", return_value=config),
            patch.object(
                membership_guard, "check_membership_detail", AsyncMock(return_value=SimpleNamespace(allowed=False))
            ) as check,
        ):
            self.assertFalse(await _real_eligible(binding))
        check.assert_awaited_once_with("g", "", bot_id="bot")

    async def test_eligibility_rereads_private_whitelist_and_bot_connection(self) -> None:
        settings = {
            "membership": {"enabled": False, "free_bot_ids": []},
            "chat": {"private_enabled": True, "private_allowlist": []},
        }
        binding = self.scheduler.bindings[self.context.session_id]
        with (
            patch.object(access, "get_bots", return_value={"bot": self.bot}) as bots,
            patch.object(access, "can_reply", return_value=True),
            patch.object(access, "direct_reply_mode", return_value=False),
            patch.object(access, "get_manager", return_value=SimpleNamespace(get_system=lambda: settings)),
        ):
            self.assertTrue(await _real_eligible(binding))
            settings["chat"]["private_allowlist"] = ["other"]
            self.assertFalse(await _real_eligible(binding))
            settings["chat"]["private_allowlist"] = []
            settings["chat"]["private_enabled"] = False
            self.assertFalse(await _real_eligible(binding))
            settings["chat"]["private_enabled"] = True
            bots.return_value = {"bot": SimpleNamespace(self_id="bot")}
            self.assertFalse(await _real_eligible(binding))


_real_eligible = continuation._eligible
