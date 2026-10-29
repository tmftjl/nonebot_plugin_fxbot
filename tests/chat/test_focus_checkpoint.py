"""Focus 当前检查点的工具中断、会话进度和压缩来源恢复。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from src.plugins.nonebot_plugin_fxbot.chat.focus import group_runtime
from src.plugins.nonebot_plugin_fxbot.chat.focus.context import CHECKPOINT_VERSION, FocusEntry, FocusCheckpointStore
from src.plugins.nonebot_plugin_fxbot.chat.memory.trimmed import trimmed_learning


class FocusCheckpointTests(unittest.IsolatedAsyncioTestCase):
    """使用实际文件验证重启恢复不会自动重放或丢失处理进度。"""

    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.store = FocusCheckpointStore("test-checkpoint", Path(folder.name))
        self.group = group_runtime.FocusGroupRuntime(self.store.scope_id, store=self.store)

    async def test_interrupted_batch_is_closed_only_in_saved_snapshot(self) -> None:
        batch = FocusEntry(
            [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {"id": "done", "function": {"name": "read"}},
                        {"id": "unfinished", "function": {"name": "write"}},
                    ],
                },
                {"role": "tool", "tool_call_id": "done", "name": "read", "content": '{"ok":true}'},
            ],
            {"session-a"},
        )
        self.group.entries = [batch]
        await self.group.checkpoint()
        self.assertEqual(len(batch.messages), 2)
        restored = group_runtime.FocusGroupRuntime(self.store.scope_id, store=self.store)
        with patch.object(restored.tools, "invoke", AsyncMock()) as invoke:
            await restored.restore()
        invoke.assert_not_awaited()
        [entry] = restored.entries
        self.assertEqual(entry.messages[:2], batch.messages)
        interrupted = entry.messages[-1]
        self.assertEqual(interrupted["tool_call_id"], "unfinished")
        self.assertEqual(json.loads(interrupted["content"])["execution_state"], "unknown")
        self.assertIn("禁止自动重放", interrupted["content"])

    async def test_watermarks_and_mentions_survive_restart(self) -> None:
        self.group.entries = [
            FocusEntry(
                [{"role": "system", "content": "摘要"}],
                {"session-a", "session-b"},
                is_at=True,
                watermarks={"session-a": 2, "session-b": 7},
                inputs={"session-b": [[7, "b7"]]},
                sources={"session-a": ["a2"]},
            )
        ]
        self.group._considered = {"session-a": 2, "session-b": 6}
        await self.group.checkpoint()
        restored = group_runtime.FocusGroupRuntime(self.store.scope_id, store=self.store)
        await restored.restore()
        self.assertEqual(restored.entries[0].progress, {"session-a": 2, "session-b": 7})
        self.assertTrue(restored.entries[0].is_at)
        self.assertFalse(restored._has_unconsidered("session-a"))
        self.assertTrue(restored._has_unconsidered("session-b"))

    async def test_compacted_sources_can_be_recalled_after_restart(self) -> None:
        self.group.entries = [
            FocusEntry(
                [{"role": "user", "content": f"发言{index}"}],
                {"session-a"},
                index + 1,
                sources={"session-a": [f"msg-{index}"]},
            )
            for index in range(12)
        ]
        self.group._planner_snapshot = object()
        with (
            patch.object(self.group, "_max_context", return_value=10),
            patch.object(group_runtime, "compact_messages", AsyncMock(return_value="有效摘要")),
            patch.object(trimmed_learning, "collect"),
        ):
            await self.group._compact()
        self.assertEqual(self.group.entries[0].progress, {"session-a": 7})
        await self.group.checkpoint()
        restored = group_runtime.FocusGroupRuntime(self.store.scope_id, store=self.store)
        await restored.restore()
        self.assertIn("msg-0", restored.entries[0].sources["session-a"])
        self.assertTrue(await restored.discard_message("session-a", "msg-0"))
        self.assertFalse(any("有效摘要" in str(entry.messages) for entry in restored.entries))

    def test_previous_checkpoint_format_is_not_converted(self) -> None:
        self.store.path.parent.mkdir(parents=True, exist_ok=True)
        original = json.dumps({"version": CHECKPOINT_VERSION - 1, "scope_id": self.store.scope_id, "entries": []})
        self.store.path.write_text(original, encoding="utf-8")
        self.assertEqual(self.store.load(), {})
        self.assertEqual(self.store.path.read_text(encoding="utf-8"), original)

    def test_missing_current_fields_are_rejected(self) -> None:
        self.store.save([FocusEntry([], {"session-a"})], {})
        payload = json.loads(self.store.path.read_text(encoding="utf-8"))
        del payload["entries"][0]["watermarks"]
        self.store.path.write_text(json.dumps(payload), encoding="utf-8")
        self.assertEqual(self.store.load(), {})
