"""合并转发：解析节点、按 path 展开，以及只有转发时的占位文本。"""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.plugins.nonebot_plugin_fxbot.chat.tools import forward
from src.plugins.nonebot_plugin_fxbot.chat.tools.types import ToolError, ToolContext
from src.plugins.nonebot_plugin_fxbot.chat.tools.runtime import ToolRuntime
from src.plugins.nonebot_plugin_fxbot.chat.message_adapter import message_text
from src.plugins.nonebot_plugin_fxbot.adapter.platforms.onebot11 import _parse_forward_nodes

_NODES = {
    "fw1": [
        {"name": "小A", "user_id": "1", "text": "第一层一", "forwards": []},
        {"name": "小B", "user_id": "2", "text": "[嵌套转发]", "forwards": ["inner"]},
    ],
    "inner": [
        {"name": "小C", "user_id": "3", "text": "嵌套层", "forwards": []},
    ],
}


def _event(*segments) -> SimpleNamespace:
    return SimpleNamespace(get_plaintext=lambda: "", get_message=lambda: list(segments))


class ParseForwardNodesTest(unittest.TestCase):
    def test_wrapped_payload_with_card_and_nested_forward(self) -> None:
        payload = {
            "messages": [
                {"sender": {"card": "小A", "user_id": 1}, "message": [{"type": "text", "data": {"text": "hello"}}]},
                {
                    "sender": {"nickname": "小B", "user_id": 2},
                    "message": [{"type": "image", "data": {}}, {"type": "forward", "data": {"id": "inner"}}],
                },
            ]
        }
        nodes = _parse_forward_nodes(payload)
        self.assertEqual(nodes[0]["name"], "小A")
        self.assertEqual(nodes[0]["text"], "hello")
        self.assertEqual(nodes[1]["text"], "[图片][嵌套转发]")
        self.assertEqual(nodes[1]["forwards"], ["inner"])

    def test_plain_list_payload_and_string_content(self) -> None:
        nodes = _parse_forward_nodes([{"sender": {"user_name": "小C"}, "content": "纯字符串"}])
        self.assertEqual(nodes[0]["name"], "小C")
        self.assertEqual(nodes[0]["text"], "纯字符串")

    def test_unexpected_payload(self) -> None:
        self.assertEqual(_parse_forward_nodes(None), [])


class ForwardToolTest(unittest.IsolatedAsyncioTestCase):
    def _runtime(self, event=None) -> ToolRuntime:
        return ToolRuntime(bot=object(), event=event)

    async def _fake_forward(self, bot, forward_id):
        return _NODES[forward_id]

    async def test_reads_forward_from_current_message(self) -> None:
        event = _event(SimpleNamespace(type="forward", data={"id": "fw1"}))
        with patch.object(forward, "get_forward_messages", self._fake_forward):
            text = await forward.view_forward_message_tool(ToolContext(), self._runtime(event))
        self.assertIn("共 2 条", text)
        self.assertIn("1. 小A(1)：第一层一", text)
        self.assertIn("path=[1]", text)

    async def test_expands_nested_path(self) -> None:
        event = _event(SimpleNamespace(type="forward", data={"id": "fw1"}))
        with patch.object(forward, "get_forward_messages", self._fake_forward):
            text = await forward.view_forward_message_tool(ToolContext(), self._runtime(event), path=[1])
        self.assertIn("1. 小C(3)：嵌套层", text)
        self.assertNotIn("第一层一", text)

    async def test_message_without_forward(self) -> None:
        event = _event(SimpleNamespace(type="text", data={"text": "hi"}))
        with self.assertRaises(ToolError):
            await forward.view_forward_message_tool(ToolContext(), self._runtime(event))

    async def test_path_must_be_non_negative_integers(self) -> None:
        with self.assertRaises(ToolError):
            forward._path([0, -1])
        with self.assertRaises(ToolError):
            forward._path([True])
        with self.assertRaises(ToolError):
            forward._path("0")


class MessageTextTest(unittest.TestCase):
    def test_forward_only_message_gets_placeholder(self) -> None:
        event = _event(SimpleNamespace(type="forward", data={"id": "fw1"}))
        self.assertEqual(message_text(event), "[合并转发消息]")

    def test_bare_mention_gets_placeholder(self) -> None:
        event = SimpleNamespace(
            get_plaintext=lambda: "",
            get_message=lambda: [SimpleNamespace(type="at", data={"qq": "bot"})],
            group_id="g1",
            is_tome=lambda: True,
        )
        self.assertEqual(message_text(event), "[@了你]")

    def test_empty_group_message_without_mention_stays_empty(self) -> None:
        event = SimpleNamespace(get_plaintext=lambda: "", get_message=lambda: [], group_id="g1", is_tome=lambda: False)
        self.assertEqual(message_text(event), "")

    def test_plain_text_unchanged(self) -> None:
        event = SimpleNamespace(get_plaintext=lambda: "你好", get_message=lambda: [])
        self.assertEqual(message_text(event), "你好")


if __name__ == "__main__":
    asyncio.run(unittest.main())
