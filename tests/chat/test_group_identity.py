"""OneBot 群身份适配、短缓存及模型背景隔离回归。"""

from __future__ import annotations

import time
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.plugins.nonebot_plugin_fxbot import adapter
from src.plugins.nonebot_plugin_fxbot.chat.focus import group_runtime
from src.plugins.nonebot_plugin_fxbot.chat.types import ChatRequest
from src.plugins.nonebot_plugin_fxbot.utils.cache import cache
from src.plugins.nonebot_plugin_fxbot.chat.group_identity import group_identity_prompt
from src.plugins.nonebot_plugin_fxbot.chat.replyer.prompt import build_persona_text
from src.plugins.nonebot_plugin_fxbot.adapter.platforms.qq import QQOfficialMessageAdapter
from src.plugins.nonebot_plugin_fxbot.adapter.platforms.onebot11 import OneBotV11MessageAdapter
from src.plugins.nonebot_plugin_fxbot.adapter.platforms.onebot12 import OneBotV12MessageAdapter


def request(group_id: str | None = "101", role: str | None = None) -> ChatRequest:
    """构造来源用户和机器人角色明确分离的请求。"""
    return ChatRequest(
        f"bot:group:{group_id}",
        "999",
        group_id,
        "你是管理员吗",
        "turn",
        "msg",
        "mention",
        metadata={"bot_group_role": role},
    )


class GroupRoleAdapterTests(unittest.IsolatedAsyncioTestCase):
    """使用真实平台适配实现，但不连接真实机器人。"""

    def setUp(self) -> None:
        cache.clear_prefix(("adapter_bot_group_role",))
        self.addCleanup(cache.clear_prefix, ("adapter_bot_group_role",))
        self.bot = SimpleNamespace(self_id="501", adapter=SimpleNamespace())
        self.platform = OneBotV11MessageAdapter()
        self.api = self.enterContext(patch.object(self.platform, "_api", AsyncMock()))
        self.enterContext(patch.object(adapter, "get_platform_adapter", return_value=self.platform))

    async def test_onebot_member_role_requires_explicit_valid_role(self) -> None:
        for role in ("owner", "admin", "member", None, "unknown"):
            with self.subTest(role=role):
                self.api.return_value = {"user_id": 999, "role": role}
                result = await self.platform.get_group_member_role(self.bot, "101", "999")
                self.assertEqual(result, role if role in {"owner", "admin", "member"} else None)
        self.api.return_value = {"user_id": 888, "role": "owner"}
        self.assertIsNone(await self.platform.get_group_member_role(self.bot, "101", "999"))

    async def test_bot_query_uses_self_id_not_message_author(self) -> None:
        self.bot.event = SimpleNamespace(sender={"user_id": 999, "role": "owner"})
        self.api.return_value = {"user_id": 501, "role": "member"}
        self.assertEqual(await adapter.get_bot_group_role(self.bot, "101"), "member")
        self.api.assert_awaited_once_with(self.bot, "get_group_member_info", group_id=101, user_id=501)

    async def test_success_cache_is_five_minutes_and_refreshes_on_next_use(self) -> None:
        self.api.return_value = {"user_id": 501, "role": "admin"}
        self.assertEqual(await adapter.get_bot_group_role(self.bot, "101"), "admin")
        for _ in range(10):
            self.assertEqual(await adapter.get_bot_group_role(self.bot, "101"), "admin")
        self.api.assert_awaited_once()
        [(key, expiry, _, _)] = cache.keys("adapter_bot_group_role")
        self.assertAlmostEqual(expiry - time.monotonic(), 300, delta=1)
        cache.set(key, {"role": "admin"}, ttl=-1)
        self.api.return_value = {"user_id": 501, "role": "member"}
        self.assertEqual(await adapter.get_bot_group_role(self.bot, "101"), "member")
        self.assertEqual(self.api.await_count, 2)

    async def test_failure_is_cached_for_thirty_seconds_without_guessing(self) -> None:
        self.api.side_effect = RuntimeError("API unavailable")
        self.assertIsNone(await adapter.get_bot_group_role(self.bot, "101"))
        self.assertIsNone(await adapter.get_bot_group_role(self.bot, "101"))
        self.api.assert_awaited_once()
        [(_, expiry, _, _)] = cache.keys("adapter_bot_group_role")
        self.assertAlmostEqual(expiry - time.monotonic(), 30, delta=1)

    async def test_cache_does_not_mix_groups_accounts_or_platforms(self) -> None:
        class AnotherPlatform:
            """区分平台类型的测试标识。"""

        self.api.return_value = {"role": "admin"}
        await adapter.get_bot_group_role(self.bot, "101")
        await adapter.get_bot_group_role(self.bot, "102")
        await adapter.get_bot_group_role(SimpleNamespace(self_id="502", adapter=SimpleNamespace()), "101")
        await adapter.get_bot_group_role(SimpleNamespace(self_id="501", adapter=AnotherPlatform()), "101")
        self.assertEqual(self.api.await_count, 4)

    async def test_timeout_cancels_query_and_adds_no_fake_role(self) -> None:
        cancelled = asyncio.Event()
        original_wait = asyncio.wait_for

        async def slow(*args, **kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        async def quick_timeout(task, timeout):
            return await original_wait(task, timeout=0.001)

        self.api.side_effect = slow
        with patch.object(adapter.asyncio, "wait_for", quick_timeout):
            self.assertIsNone(await adapter.get_bot_group_role(self.bot, "101"))
        self.assertTrue(cancelled.is_set())
        self.assertIsNone(await adapter.get_bot_group_role(self.bot, "101"))
        self.api.assert_awaited_once()

    async def test_private_and_missing_bot_do_not_query(self) -> None:
        self.assertIsNone(await adapter.get_bot_group_role(self.bot, None))
        self.assertIsNone(await adapter.get_bot_group_role(None, "101"))
        self.api.assert_not_awaited()

    async def test_qq_and_onebot_v12_do_not_claim_implemented_capability(self) -> None:
        for platform in (QQOfficialMessageAdapter(), OneBotV12MessageAdapter()):
            cache.clear_prefix(("adapter_bot_group_role",))
            with (
                self.subTest(platform=type(platform).__name__),
                patch.object(adapter, "get_platform_adapter", return_value=platform),
            ):
                self.assertIsNone(await adapter.get_bot_group_role(self.bot, "101"))
        self.api.assert_not_awaited()


class GroupIdentityPromptTests(unittest.TestCase):
    """只渲染明确的自身角色，不能用于授予消息作者权限。"""

    def test_role_labels_are_scoped_to_the_current_group(self) -> None:
        for role, label in (("member", "普通成员"), ("admin", "管理员"), ("owner", "群主")):
            with self.subTest(role=role):
                prompt = group_identity_prompt(request(role=role))
                self.assertIn(f"群 101 的身份：{label}", prompt)
                self.assertIn("非发言人", prompt)
                self.assertIn("仅本群有效", prompt)
                self.assertIn("不替代工具授权", prompt)
                self.assertLess(len(prompt), 90)

    def test_private_unknown_and_missing_roles_add_nothing(self) -> None:
        self.assertEqual(group_identity_prompt(request(None, "owner")), "")
        self.assertEqual(group_identity_prompt(request(role="unknown")), "")
        self.assertEqual(group_identity_prompt(request()), "")

    def test_ordinary_planner_and_replyer_both_receive_identity(self) -> None:
        for flags in ({"planner_loop": True}, {"planner_loop": True, "planner_reply": True}):
            item = request(role="admin")
            item.metadata.update(flags)
            _, prompt = build_persona_text(item, {"persona": "小鱼"}, {})
            self.assertEqual(prompt.count("[你的群身份]"), 1)
            self.assertIn("群 101 的身份：管理员", prompt)


class FocusIdentityTests(unittest.IsolatedAsyncioTestCase):
    """共享参考中每个群分别读取自己的机器人角色。"""

    async def test_focus_references_keep_roles_in_their_own_group(self) -> None:
        group = group_runtime.FocusGroupRuntime("role-test")
        bot = SimpleNamespace(self_id="501")
        group.members = {
            "a": SimpleNamespace(bot=bot, request=request("101")),
            "b": SimpleNamespace(bot=bot, request=request("102")),
        }
        manager = SimpleNamespace(get_system=lambda: {"chat": {"persona": "小鱼"}})

        async def role(bot, group_id):
            return "admin" if group_id == "101" else "member"

        with (
            patch.object(group, "_has_unconsidered", return_value=True),
            patch.object(group_runtime, "get_config_manager", return_value=manager),
            patch.object(group_runtime, "get_bot_group_role", side_effect=role),
            patch.object(group_runtime, "_memory_block", AsyncMock(return_value="")),
        ):
            references = await group._build_references()
        self.assertIn("群 101 的身份：管理员", references[0])
        self.assertNotIn("群 102", references[0])
        self.assertIn("群 102 的身份：普通成员", references[1])
        self.assertNotIn("群 101", references[1])

    async def test_learning_references_read_only_the_target_session_history(self) -> None:
        """共享规划仍逐会话匹配黑话，不使用其他成员的原始消息冒充本会话上下文。"""
        group = group_runtime.FocusGroupRuntime("learning-test")
        group.members = {
            sid: SimpleNamespace(bot=SimpleNamespace(self_id="501"), request=request(gid))
            for sid, gid in (("a", "101"), ("b", "102"))
        }
        histories = {sid: [{"role": "user", "content": sid, "_sender_id": sid}] for sid in ("a", "b")}
        manager = SimpleNamespace(get_system=lambda: {"chat": {"persona": "小鱼"}})
        with (
            patch.object(group, "_has_unconsidered", return_value=True),
            patch.object(group_runtime, "get_config_manager", return_value=manager),
            patch.object(group_runtime, "get_bot_group_role", AsyncMock(return_value="member")),
            patch.object(group_runtime, "_memory_block", AsyncMock(return_value="")),
            patch.object(group_runtime.chat_service.session_store, "get", side_effect=lambda sid: histories[sid]),
            patch.object(group_runtime, "_learning_reference", AsyncMock(return_value="黑话参考")) as learning,
        ):
            references = await group._build_references()
        self.assertEqual(learning.await_count, 2)
        self.assertIs(learning.await_args_list[0].args[1], histories["a"])
        self.assertIs(learning.await_args_list[1].args[1], histories["b"])
        self.assertIn('session_id="a"', references[0])
        self.assertIn('session_id="b"', references[1])
