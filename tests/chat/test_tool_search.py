"""延迟工具检索：定义默认不下发，检索后按会话启用。"""

from __future__ import annotations

import json
import asyncio
import unittest

from src.plugins.nonebot_plugin_fxbot.chat.tools import tool, default_registry
from src.plugins.nonebot_plugin_fxbot.chat.replyer import generator
from src.plugins.nonebot_plugin_fxbot.chat.tools.types import ToolError, ToolContext
from src.plugins.nonebot_plugin_fxbot.chat.tools.search import tool_search_tool
from src.plugins.nonebot_plugin_fxbot.chat.tools.runtime import ToolRuntime
from src.plugins.nonebot_plugin_fxbot.chat.tools.discovery import sync_discovered, deferred_reminder

_DEFERRED = "test_deferred_mute"
_VISIBLE = "test_visible_ping"


@tool(
    name=_DEFERRED,
    title="测试禁言",
    description="测试用的成员禁言工具。",
    deferred=True,
    parameters={"type": "object", "properties": {}},
)
async def _deferred_tool(context: ToolContext, runtime: ToolRuntime) -> str:
    return "ok"


@tool(
    name=_VISIBLE,
    title="测试问候",
    description="始终下发的测试工具。",
    parameters={"type": "object", "properties": {}},
)
async def _visible_tool(context: ToolContext, runtime: ToolRuntime) -> str:
    return "ok"


class ToolSearchTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.registry = default_registry

    def test_deferred_tool_hidden_until_discovered(self) -> None:
        names = {_DEFERRED, _VISIBLE}
        session = f"tool-search-{id(self)}"
        visible_before = {item["function"]["name"] for item in generator.visible_tools(names, session)}
        self.assertIn(_VISIBLE, visible_before)
        self.assertNotIn(_DEFERRED, visible_before)

    async def test_search_enables_tool_for_session(self) -> None:
        session = f"tool-search-{id(self)}"
        context = ToolContext(session_id=session, metadata={"allowed_tools": [_DEFERRED, _VISIBLE]})
        text = await tool_search_tool(context, ToolRuntime(), query="禁言")
        self.assertIn(_DEFERRED, text)
        visible_after = {item["function"]["name"] for item in generator.visible_tools({_DEFERRED}, session)}
        self.assertIn(_DEFERRED, visible_after)
        other = {item["function"]["name"] for item in generator.visible_tools({_DEFERRED}, f"{session}-other")}
        self.assertNotIn(_DEFERRED, other)

    async def test_search_respects_allowed_names(self) -> None:
        context = ToolContext(session_id=f"tool-search-{id(self)}", metadata={"allowed_tools": [_VISIBLE]})
        text = await tool_search_tool(context, ToolRuntime(), query="禁言")
        self.assertNotIn(_DEFERRED, text)

    async def test_search_requires_query(self) -> None:
        with self.assertRaises(ToolError):
            await tool_search_tool(ToolContext(), ToolRuntime(), query=" ")

    async def test_discovery_expires_when_search_call_leaves_context(self) -> None:
        session = f"tool-context-{id(self)}"
        context = ToolContext(session_id=session, metadata={"allowed_tools": [_DEFERRED]})
        text = await tool_search_tool(context, ToolRuntime(), query="禁言")
        history = [
            {"role": "assistant", "tool_calls": [{"id": "search-call", "function": {"name": "tool_search"}}]},
            {"role": "tool", "tool_call_id": "search-call", "content": json.dumps({"ok": True, "result": text})},
        ]
        sync_discovered(session, history)
        self.assertTrue(generator.visible_tools({_DEFERRED}, session))
        self.assertFalse(deferred_reminder({_DEFERRED}, session))
        sync_discovered(session, history[1:])
        self.assertFalse(generator.visible_tools({_DEFERRED}, session))
        self.assertIn(_DEFERRED, deferred_reminder({_DEFERRED}, session))
        self.assertNotIn(_VISIBLE, deferred_reminder({_DEFERRED}, session))


if __name__ == "__main__":
    asyncio.run(unittest.main())
