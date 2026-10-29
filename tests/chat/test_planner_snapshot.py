"""摘要复用成功 Planner 请求、热重载隔离和工具响应拒收。"""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

from src.plugins.nonebot_plugin_fxbot.chat import context_summary as summaries
from src.plugins.nonebot_plugin_fxbot.chat.providers.base import ChatProvider
from src.plugins.nonebot_plugin_fxbot.chat.providers.manager import ProviderManager
from src.plugins.nonebot_plugin_fxbot.chat.providers.entities import LLMResponse


class PlannerSnapshotTests(unittest.IsolatedAsyncioTestCase):
    """从实际成功请求构造副本，压缩调用不能悄悄改变前缀或执行工具。"""

    def setUp(self) -> None:
        self.tenant = "test:planner-snapshot"
        summaries.clear_summary(self.tenant)
        self.addCleanup(summaries.clear_summary, self.tenant)
        self.provider = SimpleNamespace(
            provider_id="original",
            config={"model": "original-model"},
            get_model=lambda: "original-model",
            chat=AsyncMock(),
        )
        self.messages = [{"role": "system", "content": "原人格"}, {"role": "user", "content": "已覆盖的消息"}]
        self.tools = [{"type": "function", "function": {"name": name}} for name in ("wait", "reply")]
        self.snapshot = summaries.planner_snapshot(self.provider, self.messages, self.tools)

    def test_detached_snapshot_and_uncovered_delta_preserve_prefix(self) -> None:
        self.provider.config["model"] = "reloaded-model"
        self.messages[0]["content"] = "新人格"
        self.tools[0]["function"]["name"] = "new-tool"
        request = summaries.build_snapshot_messages(
            self.snapshot,
            "旧摘要",
            [{"role": "user", "content": "已覆盖的消息"}],
            [{"role": "tool", "name": "lookup", "tool_call_id": "call", "content": "真实结果"}],
            6000,
        )
        self.assertEqual(request[:-1], self.snapshot.messages)
        self.assertEqual(self.snapshot.messages[0]["content"], "原人格")
        self.assertEqual(self.snapshot.provider_config["model"], "original-model")
        self.assertEqual(self.snapshot.tools[0]["function"]["name"], "wait")
        self.assertNotIn("已覆盖的消息", request[-1]["content"])
        self.assertIn("真实结果", request[-1]["content"])
        self.assertIn("旧摘要", request[-1]["content"])

    async def test_compaction_uses_original_provider_and_options(self) -> None:
        self.provider.chat.return_value = LLMResponse(
            content=json.dumps(
                {"topics": ["合成话题"], "background": [], "progress": [], "pending": [], "agreements": []}
            )
        )

        @asynccontextmanager
        async def lease(provider_id, config):
            self.assertEqual((provider_id, config), ("original", {"model": "original-model"}))
            yield self.provider

        with patch.object(summaries.provider_manager, "lease_snapshot", lease):
            result = await summaries.compact_messages(
                self.tenant,
                [{"role": "user", "content": "新增消息"}],
                planner_request=self.snapshot,
            )
        self.assertIn("合成话题", result)
        options = self.provider.chat.await_args.kwargs
        self.assertEqual(options["messages"][:-1], self.snapshot.messages)
        self.assertEqual(options["tools"], self.snapshot.tools)
        self.assertEqual(
            (options["model"], options["temperature"], options["max_tokens"]), ("original-model", 0.7, 16384)
        )

    async def test_tool_call_response_is_never_committed(self) -> None:
        summaries._summaries[self.tenant] = "旧有效摘要"
        self.provider.chat.return_value = LLMResponse(content='{"topics":[]}', tool_calls=[{"id": "not-executed"}])

        @asynccontextmanager
        async def lease(*args):
            yield self.provider

        with patch.object(summaries.provider_manager, "lease_snapshot", lease), patch.object(summaries, "logger"):
            result = await summaries.compact_messages(
                self.tenant, [{"role": "user", "content": "新消息"}], planner_request=self.snapshot
            )
        self.assertEqual(result, "旧有效摘要")
        self.assertEqual(summaries.current_summary(self.tenant), "旧有效摘要")

    async def test_missing_planner_snapshot_does_not_request_another_model(self) -> None:
        with patch.object(summaries.provider_manager, "lease_snapshot") as lease:
            self.assertEqual(await summaries.compact_messages(self.tenant, [{"role": "user", "content": "旧消息"}]), "")
        lease.assert_not_called()

    async def test_invalidated_focus_result_does_not_replace_current_summary(self) -> None:
        self.provider.chat.return_value = LLMResponse(
            content=json.dumps(
                {"topics": ["已被移出的会话"], "background": [], "progress": [], "pending": [], "agreements": []}
            )
        )

        @asynccontextmanager
        async def lease(*args):
            yield self.provider

        with patch.object(summaries.provider_manager, "lease_snapshot", lease):
            await summaries.compact_messages(
                self.tenant,
                [{"role": "user", "content": "旧消息"}],
                planner_request=self.snapshot,
                is_valid=lambda: False,
            )
        self.assertEqual(summaries.current_summary(self.tenant), "")


class SnapshotProviderLeaseTests(unittest.IsolatedAsyncioTestCase):
    """临时快照实例结束后必须关闭，且不取当前路由配置。"""

    async def test_snapshot_instance_closes_on_error(self) -> None:
        class Provider(ChatProvider):
            """只用于验证租约生命周期的对话实现。"""

            async def chat(self, *args, **kwargs):
                return LLMResponse(content="done")

        provider = Provider("snapshot", {"model": "old"})
        provider.close = AsyncMock()
        manager = ProviderManager()
        with patch.object(manager, "_create_provider", return_value=provider) as create:
            with self.assertRaises(RuntimeError):
                async with manager.lease_snapshot("snapshot", {"model": "old"}):
                    raise RuntimeError("cancelled request")
        create.assert_called_once_with("snapshot", {"model": "old"})
        provider.close.assert_awaited_once()
