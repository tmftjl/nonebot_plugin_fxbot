"""send_file：写入固定目录并发送，只接受模型生成的内容。"""

from __future__ import annotations

import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import AsyncMock, patch

from src.plugins.nonebot_plugin_fxbot.adapter import UnsupportedCapability
from src.plugins.nonebot_plugin_fxbot.chat.tools import file as send_file
from src.plugins.nonebot_plugin_fxbot.chat.tools.types import ToolError, ToolContext
from src.plugins.nonebot_plugin_fxbot.chat.tools.runtime import ToolRuntime
from src.plugins.nonebot_plugin_fxbot.chat.tools.registry import default_registry


def _runtime(event=None) -> ToolRuntime:
    return ToolRuntime(bot=object(), event=event or SimpleNamespace(group_id=1, user_id=2))


class FilenameTest(unittest.TestCase):
    def test_accepts_plain_names_with_any_extension(self) -> None:
        self.assertEqual(send_file._filename(" 报告.md "), "报告.md")
        self.assertEqual(send_file._filename("deploy.sh"), "deploy.sh")

    def test_rejects_paths_and_empty_names(self) -> None:
        for value in ("", "  ", ".", "..", "../x.md", "a/b.md", "a\\b.md", "/etc/passwd", "a" * 101):
            with self.subTest(value=value), self.assertRaises(ToolError):
                send_file._filename(value)

    def test_registered_by_default(self) -> None:
        spec = default_registry.get("send_file")
        self.assertIsNotNone(spec)
        self.assertFalse(spec.deferred)


class SendFileToolTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._folder = tempfile.TemporaryDirectory()
        self.addCleanup(self._folder.cleanup)
        patcher = patch.object(send_file, "cache_dir", lambda name=None: Path(self._folder.name))
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_writes_into_fixed_directory_and_sends(self) -> None:
        sender = AsyncMock(return_value=None)
        with patch.object(send_file, "send_file_to_target", sender):
            result = await send_file.send_file_tool(ToolContext(), _runtime(), name="报告.md", content="正文")
        self.assertTrue(result["sent"])
        path = Path(self._folder.name) / "报告.md"
        self.assertEqual(path.read_text(encoding="utf-8"), "正文")
        self.assertEqual(sender.await_args.args[2], path)
        self.assertEqual(sender.await_args.args[3], "报告.md")

    async def test_rejects_path_like_name_before_writing(self) -> None:
        sender = AsyncMock()
        with patch.object(send_file, "send_file_to_target", sender):
            with self.assertRaises(ToolError):
                await send_file.send_file_tool(ToolContext(), _runtime(), name="../x.md", content="x")
        sender.assert_not_awaited()
        self.assertEqual(list(Path(self._folder.name).iterdir()), [])

    async def test_rejects_oversized_content(self) -> None:
        with self.assertRaises(ToolError):
            await send_file.send_file_tool(
                ToolContext(), _runtime(), name="big.txt", content="x" * (send_file.MAX_FILE_CHARS + 1)
            )

    async def test_reports_unsupported_platform(self) -> None:
        with patch.object(send_file, "send_file_to_target", AsyncMock(side_effect=UnsupportedCapability("发送文件"))):
            with self.assertRaises(ToolError):
                await send_file.send_file_tool(ToolContext(), _runtime(), name="a.md", content="x")
