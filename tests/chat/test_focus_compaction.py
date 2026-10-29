"""Focus 轮后裁切阈值、学习归属和清空竞态。"""

from __future__ import annotations

import unittest
from uuid import uuid4
from unittest.mock import AsyncMock, patch

from src.plugins.nonebot_plugin_fxbot.chat.focus import group_runtime
from src.plugins.nonebot_plugin_fxbot.chat.focus.context import FocusEntry
from src.plugins.nonebot_plugin_fxbot.chat.memory.trimmed import trimmed_learning


class FocusCompactionTests(unittest.IsolatedAsyncioTestCase):
    """压缩内部工具消息不计数，各会话学习只获得自己的原始消息。"""

    def runtime(self, count: int) -> group_runtime.FocusGroupRuntime:
        """构造交替来源的独立共享时间线。"""
        runtime = group_runtime.FocusGroupRuntime(f"test-focus-compact:{uuid4().hex}")
        runtime._planner_snapshot = object()
        for index in range(count):
            tenant = "session-a" if index % 2 else "session-b"
            runtime.entries.append(
                FocusEntry(
                    [{"role": "user", "content": str(index)}],
                    {tenant},
                    sources={tenant: [f"msg-{index}"]},
                )
            )
        return runtime

    async def test_threshold_counts_visible_messages_and_learning_keeps_sources(self) -> None:
        runtime = self.runtime(11)
        runtime.entries.insert(1, FocusEntry([{"role": "assistant", "_planner_internal": True}], {"session-a"}))
        with (
            patch.object(runtime, "_max_context", return_value=10),
            patch.object(group_runtime, "compact_messages", AsyncMock(return_value="摘要")) as compact,
            patch.object(trimmed_learning, "collect") as collect,
        ):
            await runtime._compact()
            compact.assert_not_awaited()
            runtime.entries.append(
                FocusEntry([{"role": "user", "content": "latest"}], {"session-a"}, sources={"session-a": ["latest"]})
            )
            await runtime._compact()
        self.assertEqual(len(runtime.entries), 6)
        self.assertEqual(runtime.entries[-1].messages[0]["content"], "latest")
        by_tenant = {call.args[0]: call.args[1] for call in collect.call_args_list}
        self.assertEqual(set(by_tenant), {"session-a", "session-b"})
        for tenant, messages in by_tenant.items():
            for message in messages:
                for identifier in message["_source_ids"]:
                    index = int(identifier.split("-")[-1])
                    self.assertEqual(tenant, "session-a" if index % 2 else "session-b")

    async def test_clear_during_summary_cannot_restore_old_entries(self) -> None:
        runtime = self.runtime(12)

        async def compact(*args, **kwargs):
            runtime.entries.clear()
            return "过时摘要"

        with (
            patch.object(runtime, "_max_context", return_value=10),
            patch.object(group_runtime, "compact_messages", AsyncMock(side_effect=compact)),
            patch.object(trimmed_learning, "collect") as collect,
        ):
            await runtime._compact()
        self.assertEqual(runtime.entries, [])
        collect.assert_not_called()
