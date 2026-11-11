"""入站路由互斥、事件级单次判定、取消隔离与真实平台身份回归。"""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.plugins.nonebot_plugin_fxbot.chat import hooks, router


class IngressDecisionTests(unittest.IsolatedAsyncioTestCase):
    """同事件的重复或并发询问不能重复扣费、重复审计或恢复已拒绝请求。"""

    def setUp(self) -> None:
        self.enterContext(patch.object(hooks, "_hooks", {}))
        self.enterContext(patch.object(hooks, "logger", Mock()))
        self.bot = SimpleNamespace(self_id="bot", adapter=SimpleNamespace(get_name=lambda: "real-platform"))
        self.event = SimpleNamespace(user_id="user", group_id=None, text="message")
        self.enterContext(patch.object(router, "session_id_for_event", return_value="namespace:bot:private:user"))
        self.enterContext(patch.object(router, "event_user_id", side_effect=lambda event: event.user_id))
        self.enterContext(patch.object(router, "event_group_id", side_effect=lambda event: event.group_id))
        self.enterContext(patch.object(router, "message_text", side_effect=lambda event: event.text))

    def register(self, kind, callback):
        self.addCleanup(hooks.register_chat_hook(kind, "test", callback))

    async def allowed(self, path="reply", event=None):
        return await router._ingress_allowed(self.bot, self.event if event is None else event, path=path)

    async def test_first_path_decides_once_and_notifies_once(self) -> None:
        guard = Mock(return_value=True)
        observer = Mock()
        self.register("ingress_guard", guard)
        self.register("observer", observer)
        self.assertTrue(await self.allowed("context"))
        self.assertTrue(await self.allowed("reply"))
        guard.assert_called_once()
        observer.assert_called_once()
        self.assertEqual(guard.call_args.args[1]["path"], "context")
        self.assertEqual(observer.call_args.args[0], "ingress_accepted")
        self.assertEqual(guard.call_args.args[0].platform, "real-platform")

    async def test_rejection_cannot_be_reversed_by_later_path_or_hook_replacement(self) -> None:
        self.register("ingress_guard", lambda *args: False)
        self.assertFalse(await self.allowed("context"))
        self.register("ingress_guard", lambda *args: True)
        self.assertFalse(await self.allowed("reply"))
        self.assertTrue(await self.allowed("reply", SimpleNamespace(user_id="user", group_id=None, text="next")))

    async def test_unloading_all_hooks_does_not_restore_rejected_event(self) -> None:
        remove = hooks.register_chat_hook("ingress_guard", "temporary", lambda *args: False)
        self.addCleanup(remove)
        self.assertFalse(await self.allowed("context"))
        remove()
        self.assertFalse(await self.allowed("reply"))
        self.assertTrue(await self.allowed("reply", SimpleNamespace(user_id="user", group_id=None, text="next")))

    async def test_concurrent_requests_share_one_guard_and_waiter_cancellation_is_isolated(self) -> None:
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def guard(context, details):
            calls.append(details["path"])
            entered.set()
            await release.wait()
            return True

        self.register("ingress_guard", guard)
        first = asyncio.create_task(self.allowed("context"))
        await entered.wait()
        waiter = asyncio.create_task(self.allowed("reply"))
        await asyncio.sleep(0)
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        release.set()
        self.assertTrue(await first)
        self.assertTrue(await self.allowed("reply"))
        self.assertEqual(calls, ["context"])

    async def test_cancelling_owner_cannot_retry_same_event_as_new_request(self) -> None:
        entered = asyncio.Event()

        async def guard(*args):
            entered.set()
            await asyncio.Event().wait()

        self.register("ingress_guard", guard)
        owner = asyncio.create_task(self.allowed())
        await entered.wait()
        owner.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await owner
        with self.assertRaises(asyncio.CancelledError):
            await self.allowed()

    async def test_recursive_async_guard_fails_closed_without_waiting_for_timeout(self) -> None:
        async def guard(*args):
            return await self.allowed()

        self.register("ingress_guard", guard)
        self.assertFalse(await asyncio.wait_for(self.allowed(), 0.5))

    async def test_ordinary_reply_and_context_routes_are_mutually_exclusive(self) -> None:
        cfg = {
            "enabled": True,
            "private_enabled": True,
            "private_allowlist": [],
            "group_allowlist": [],
            "reply_timing": {},
        }
        cases = [(None, False, ""), ("group", True, ""), ("group", False, "persona"), ("group", False, "")]
        with (
            patch.object(router, "_chat_cfg", return_value=cfg),
            patch.object(router, "should_process_fxbot_message", return_value=True),
            patch.object(router, "can_reply", return_value=True),
            patch.object(router, "direct_reply_mode", return_value=False),
            patch.object(router, "_message_filtered", return_value=False),
            patch.object(router, "history_limit", return_value=20),
        ):
            for group, at, persona in cases:
                with (
                    self.subTest(group=group, at=at, persona=persona),
                    patch.object(router, "_is_to_me", return_value=at),
                    patch.object(router, "_persona_trigger", return_value=persona),
                ):
                    event = SimpleNamespace(user_id="user", group_id=group, text="message")
                    self.assertNotEqual(
                        await router._context_rule(self.bot, event), await router._ai_fallback_rule(self.bot, event)
                    )

    async def test_config_change_between_routes_does_not_repeat_guard_or_audit(self) -> None:
        cfg = {"enabled": True, "group_allowlist": [], "reply_timing": {"inevitable_at_reply": False}}
        event = SimpleNamespace(user_id="user", group_id="group", text="message")
        guard = Mock(return_value=True)
        observer = Mock()
        self.register("ingress_guard", guard)
        self.register("observer", observer)
        with (
            patch.object(router, "_chat_cfg", return_value=cfg),
            patch.object(router, "should_process_fxbot_message", return_value=True),
            patch.object(router, "can_reply", return_value=True),
            patch.object(router, "direct_reply_mode", return_value=False),
            patch.object(router, "_message_filtered", return_value=False),
            patch.object(router, "history_limit", return_value=20),
            patch.object(router, "_is_to_me", return_value=True),
            patch.object(router, "_persona_trigger", return_value=""),
        ):
            self.assertTrue(await router._context_rule(self.bot, event))
            cfg["reply_timing"]["inevitable_at_reply"] = True
            self.assertTrue(await router._ai_fallback_rule(self.bot, event))
        guard.assert_called_once()
        observer.assert_called_once()

    async def test_same_event_different_bot_does_not_share_decision(self) -> None:
        guard = Mock(return_value=True)
        self.register("ingress_guard", guard)
        await self.allowed()
        self.bot.self_id = "another-bot"
        await self.allowed()
        self.assertEqual(guard.call_count, 2)
