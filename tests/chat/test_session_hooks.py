"""通用会话解析、事件内一致性及隔离策略的回归测试。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, AsyncMock, patch

from nonebot.adapters.onebot.v11 import PrivateMessageEvent
from src.plugins.nonebot_plugin_fxbot.chat import session_hooks as hooks, message_adapter
from src.plugins.nonebot_plugin_fxbot.chat.focus import manager as focus_module
from src.plugins.nonebot_plugin_fxbot.chat.types import ChatRequest
from src.plugins.nonebot_plugin_fxbot.adapter.platforms.qq import QQOfficialMessageAdapter
from src.plugins.nonebot_plugin_fxbot.chat.memory.learning import share_tenants, tenant_target
from src.plugins.nonebot_plugin_fxbot.adapter.platforms.onebot11 import OneBotV11MessageAdapter
from src.plugins.nonebot_plugin_fxbot.adapter.platforms.onebot12 import OneBotV12MessageAdapter

BASE = "onebot11:bot:private:user"
ISOLATED = "onebot11:bot#isolated:private:user"


class SessionResolverTests(unittest.TestCase):
    """插件只改变命名空间，不能改变真实目标，解析结果属于事件而非用户缓存。"""

    def setUp(self) -> None:
        self.enterContext(patch.object(hooks, "_resolvers", {}))
        self.enterContext(patch.object(hooks, "_policies", {}))
        self.bot = SimpleNamespace(self_id="bot")
        self.event = SimpleNamespace()

    def register(self, name, resolver):
        self.addCleanup(hooks.register_session_resolver(name, resolver))

    def test_without_hooks_preserves_default(self) -> None:
        self.assertEqual(hooks.resolve_session_id(self.bot, self.event, BASE), BASE)

    def test_platform_spaces_are_preserved_in_derived_sessions(self) -> None:
        self.register("server", lambda bot, event, base: base.replace(":3878466282:", ":3878466282#server:"))
        for platform in ("OneBot V11", "OneBot V12", "QQ"):
            for kind in ("private", "group"):
                base = f"{platform}:3878466282:{kind}:3226795632"
                expected = f"{platform}:3878466282#server:{kind}:3226795632"
                with self.subTest(platform=platform, kind=kind):
                    event = SimpleNamespace()
                    self.assertEqual(hooks.resolve_session_id(self.bot, event, base), expected)
                    self.assertEqual(hooks.resolve_session_id(self.bot, event, base), expected)

    def test_platform_spaces_do_not_allow_invalid_namespace_or_identity(self) -> None:
        base = "OneBot V11:bot:private:user"
        for candidate in (
            "OneBot V11:bot#bad name:private:user",
            "OneBot V11:bot#bad\tname:private:user",
            "OneBot V11:bot#bad\nname:private:user",
            "OneBot V11:bot#bad\u3000name:private:user",
            " OneBot V11:bot#server:private:user",
            "OneBot V11:bot#server:private:user ",
            "OneBot V12:bot#server:private:user",
            "OneBot V11:bot#server:group:user",
            "OneBot V11:bot#server:private:other",
        ):
            with self.subTest(candidate=candidate):
                self.register("invalid", lambda *args: candidate)
                with self.assertRaises(ValueError):
                    hooks.resolve_session_id(self.bot, SimpleNamespace(), base)

    def test_registration_during_event_does_not_change_already_resolved_default(self) -> None:
        self.assertEqual(hooks.resolve_session_id(self.bot, self.event, BASE), BASE)
        self.register("plugin", lambda *args: ISOLATED)
        self.assertEqual(hooks.resolve_session_id(self.bot, self.event, BASE), BASE)
        self.assertEqual(hooks.resolve_session_id(self.bot, SimpleNamespace(), BASE), ISOLATED)

    def test_first_nonempty_result_wins(self) -> None:
        last = Mock(return_value=BASE)
        self.register("empty", lambda *args: "")
        self.register("none", lambda *args: None)
        self.register("isolated", lambda *args: ISOLATED)
        self.register("last", last)
        self.assertEqual(hooks.resolve_session_id(self.bot, self.event, BASE), ISOLATED)
        last.assert_not_called()

    def test_event_result_survives_mode_change_but_next_event_rechecks(self) -> None:
        mode = {"enabled": False}
        resolver = Mock(side_effect=lambda *args: ISOLATED if mode["enabled"] else None)
        self.register("mode", resolver)
        self.assertEqual(hooks.resolve_session_id(self.bot, self.event, BASE), BASE)
        mode["enabled"] = True
        self.assertEqual(hooks.resolve_session_id(self.bot, self.event, BASE), BASE)
        self.assertEqual(hooks.resolve_session_id(self.bot, SimpleNamespace(), BASE), ISOLATED)
        self.assertEqual(resolver.call_count, 2)

    def test_same_event_is_scoped_by_bot_base_id(self) -> None:
        self.register("derive", lambda bot, event, base: base.replace(":private:", "#isolated:private:"))
        first = hooks.resolve_session_id(self.bot, self.event, BASE)
        other = "onebot11:other:private:user"
        self.assertEqual(first, ISOLATED)
        self.assertEqual(
            hooks.resolve_session_id(SimpleNamespace(), self.event, other), "onebot11:other#isolated:private:user"
        )

    def test_failed_event_cannot_fall_back_after_hook_recovery(self) -> None:
        resolver = Mock(side_effect=RuntimeError("hook down"))
        self.register("broken", resolver)
        with self.assertRaises(RuntimeError):
            hooks.resolve_session_id(self.bot, self.event, BASE)
        resolver.side_effect = None
        resolver.return_value = None
        with self.assertRaises(RuntimeError):
            hooks.resolve_session_id(self.bot, self.event, BASE)
        self.assertEqual(resolver.call_count, 1)
        self.assertEqual(hooks.resolve_session_id(self.bot, SimpleNamespace(), BASE), BASE)

    def test_invalid_results_fail_closed(self) -> None:
        for candidate in (42, False, " ", "x:private:other", "x:group:user", "other:bot:private:user", ":private:user"):
            with self.subTest(candidate=candidate):
                self.register("invalid", lambda *args: candidate)
                with self.assertRaises((TypeError, ValueError)):
                    hooks.resolve_session_id(self.bot, SimpleNamespace(), BASE)

    def test_unregister_does_not_remove_new_implementation(self) -> None:
        old = hooks.register_session_resolver("plugin", lambda *args: BASE)
        self.register("plugin", lambda *args: ISOLATED)
        old()
        old()
        self.assertEqual(hooks.resolve_session_id(self.bot, self.event, BASE), ISOLATED)

    def test_replacing_a_hook_keeps_priority(self) -> None:
        self.register("first", lambda *args: None)
        self.register("second", lambda *args: BASE)
        self.register("first", lambda *args: ISOLATED)
        self.assertEqual(hooks.resolve_session_id(self.bot, self.event, BASE), ISOLATED)

    def test_async_hooks_are_rejected_at_registration(self) -> None:
        async def resolver(*args):
            return ISOLATED

        with self.assertRaises(ValueError):
            hooks.register_session_resolver("async", resolver)
        with self.assertRaises(ValueError):
            hooks.register_session_policy("async", resolver)

    def test_recursive_resolution_is_rejected_without_infinite_recursion(self) -> None:
        self.register("recursive", lambda bot, event, base: hooks.resolve_session_id(bot, event, base))
        with self.assertRaises(RuntimeError):
            hooks.resolve_session_id(self.bot, self.event, BASE)

    def test_native_pydantic_event_supports_frozen_result(self) -> None:
        event = PrivateMessageEvent(
            time=0,
            self_id=1,
            post_type="message",
            message_type="private",
            sub_type="friend",
            user_id=2,
            message_id=3,
            message="hello",
            raw_message="hello",
            font=0,
            sender={"user_id": 2},
        )
        remove = hooks.register_session_resolver("native", lambda *args: ISOLATED)
        self.assertEqual(hooks.resolve_session_id(self.bot, event, BASE), ISOLATED)
        remove()
        self.assertEqual(hooks.resolve_session_id(self.bot, event, BASE), ISOLATED)

    def test_event_without_attribute_storage_fails_closed_with_hooks(self) -> None:
        self.register("isolate", lambda *args: ISOLATED)
        with self.assertRaises((TypeError, AttributeError)):
            hooks.resolve_session_id(self.bot, object(), BASE)

    def test_adapters_preserve_target_identity_and_freeze_namespace(self) -> None:
        for adapter, platform in (
            (OneBotV11MessageAdapter(), "OneBot V11"),
            (OneBotV12MessageAdapter(), "OneBot V12"),
            (QQOfficialMessageAdapter(), "QQ"),
        ):
            for group_id in (None, "group"):
                with self.subTest(adapter=type(adapter).__name__, group=group_id):
                    event = SimpleNamespace(user_id="user", group_id=group_id, message_id="m1", id="m1")
                    mode = {"enabled": True}
                    self.register(
                        "namespace",
                        lambda bot, event, base: base.replace(":bot:", ":bot#isolated:") if mode["enabled"] else None,
                    )
                    with (
                        patch.object(message_adapter, "get_platform_adapter", return_value=adapter),
                        patch.object(message_adapter, "platform_for_bot", return_value=platform),
                    ):
                        first = message_adapter.event_identity(self.bot, event)
                        mode["enabled"] = False
                        self.assertEqual(message_adapter.session_id_for_event(self.bot, event), first.session_id)
                    target = tenant_target(first.session_id)
                    self.assertEqual(target.item_id, group_id or "user")
                    self.assertEqual(target.is_group, bool(group_id))
                    self.assertEqual(first.message_id, "m1")
                    self.assertEqual(first.dedupe_key, "msg:m1")
                    self.assertIn("#isolated", target.namespace)


class SessionPolicyTests(unittest.IsolatedAsyncioTestCase):
    """策略动态取交集，共享两端都检查，Focus 直接注册也不能绕过。"""

    def setUp(self) -> None:
        self.enterContext(patch.object(hooks, "_policies", {}))

    def register(self, name, resolver):
        self.addCleanup(hooks.register_session_policy(name, resolver))

    def test_defaults_do_not_restrict_existing_sessions(self) -> None:
        self.assertEqual(hooks.session_policy(BASE), hooks.SessionPolicy())
        self.assertTrue(hooks.can_share_sessions(BASE, ISOLATED))

    def test_combines_restrictions_and_rechecks_dynamic_state(self) -> None:
        state = {"allow": False}
        self.register("focus", lambda sid: hooks.SessionPolicy(allow_focus=state["allow"]))
        self.register("sharing", lambda sid: hooks.SessionPolicy(allow_sharing=False))
        self.register("default", lambda sid: None)
        self.assertEqual(hooks.session_policy(BASE), hooks.SessionPolicy(False, False))
        state["allow"] = True
        self.assertEqual(hooks.session_policy(BASE), hooks.SessionPolicy(True, False))

    def test_policy_failure_and_bad_types_fail_closed(self) -> None:
        for resolver in (
            Mock(side_effect=RuntimeError("unavailable")),
            lambda sid: {},
            lambda sid: hooks.SessionPolicy(1, True),
        ):
            with self.subTest(resolver=resolver):
                self.register("bad", resolver)
                self.assertEqual(hooks.session_policy(BASE), hooks.SessionPolicy(False, False))

    def test_response_timing_is_opt_in_dynamic_and_intersected(self) -> None:
        state = {"instant": False}
        self.register(
            "timing", lambda sid: hooks.SessionPolicy(allow_debounce=not state["instant"], allow_typing=False)
        )
        self.register("defaults", lambda sid: hooks.SessionPolicy())
        self.assertEqual(hooks.session_policy(BASE), hooks.SessionPolicy(allow_typing=False))
        state["instant"] = True
        self.assertEqual(hooks.session_policy(BASE), hooks.SessionPolicy(allow_debounce=False, allow_typing=False))

    def test_typo_is_intersected(self) -> None:
        self.register("typo", lambda sid: hooks.SessionPolicy(allow_typo=False))
        self.assertEqual(hooks.session_policy(BASE), hooks.SessionPolicy(allow_typo=False))

    def test_learning_sharing_blocks_both_directions(self) -> None:
        self.register("isolate", lambda sid: hooks.SessionPolicy(allow_sharing=False) if sid == ISOLATED else None)
        targets = [{"platform": "onebot11", "item_id": "*", "type": "private"}]
        known = [BASE, ISOLATED, "onebot11:bot:private:other"]
        self.assertEqual(share_tenants(ISOLATED, tenant_target(ISOLATED), targets, known), [ISOLATED])
        self.assertEqual(share_tenants(BASE, tenant_target(BASE), targets, known), [BASE, known[2]])
        self.assertTrue(hooks.can_share_sessions(ISOLATED, ISOLATED))
        self.assertFalse(hooks.can_share_sessions(BASE, ISOLATED))
        self.assertFalse(hooks.can_share_sessions(ISOLATED, BASE))

    async def test_focus_denies_request_and_direct_registration(self) -> None:
        self.register("isolate", lambda sid: hooks.SessionPolicy(allow_focus=False) if sid == ISOLATED else None)
        manager = focus_module.FocusModeManager()
        request = ChatRequest(ISOLATED, "user", None, "hello", "turn", "m1", "private", platform="onebot11")
        config = {"enabled": True, "on_private": True, "chat_whitelist": [], "groups": []}
        with (
            patch.object(focus_module, "focus_config", return_value=config),
            patch.object(manager, "detach", AsyncMock()) as detach,
        ):
            self.assertFalse(manager.is_enabled_for_request(request))
            await manager.register(SimpleNamespace(session_id=ISOLATED), request, is_at=True)
            detach.assert_awaited_once_with(ISOLATED, clear_context=True)
        self.assertEqual(manager.groups, {})

    def test_focus_sharing_policy_overrides_matching_group(self) -> None:
        self.register("isolate", lambda sid: hooks.SessionPolicy(allow_sharing=False) if sid == ISOLATED else None)
        request = ChatRequest(ISOLATED, "user", None, "hello", "turn", "m1", "private", platform="onebot11")
        groups = [{"targets": [{"platform": "onebot11", "item_id": "user", "type": "private"}]}]
        with patch.object(focus_module, "focus_config", return_value={"groups": groups}):
            self.assertEqual(focus_module._scope_key(request), "session:" + ISOLATED)
            request.session_id = BASE
            self.assertTrue(focus_module._scope_key(request).startswith("group:"))
