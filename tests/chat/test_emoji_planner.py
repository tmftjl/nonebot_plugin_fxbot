"""Planner 表情工具的候选隔离与发送回归。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.plugins.nonebot_plugin_fxbot.chat.heart_flow import emoji_tools


class EmojiPlannerTests(unittest.IsolatedAsyncioTestCase):
    """候选图只供模型选择，发送仍通过当前会话运行时。"""

    async def test_candidate_snapshots_keep_old_ids_and_are_tenant_scoped(self) -> None:
        request = SimpleNamespace(session_id="tenant-a", metadata={})
        rows = [SimpleNamespace(hash="first"), SimpleNamespace(hash="second")]
        with (
            patch.object(emoji_tools, "emoji_catalog", AsyncMock(return_value=rows)) as catalog,
            patch.object(emoji_tools, "emoji_collage", return_value="data:image/png;base64,test"),
            patch.object(emoji_tools, "planner_has_vision", return_value=True),
        ):
            first, additions = await emoji_tools.show_candidates(request)
            old = dict(request.metadata["planner_emoji_candidates"])
            await emoji_tools.show_candidates(request)
        self.assertTrue(first["ok"])
        self.assertEqual(additions[0]["content"][1]["type"], "image_url")
        self.assertEqual(len(request.metadata["planner_emoji_candidates"]), 4)
        self.assertTrue(old.items() <= request.metadata["planner_emoji_candidates"].items())
        catalog.assert_awaited_with("tenant-a")
