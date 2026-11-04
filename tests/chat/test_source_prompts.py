"""上游提示词、回复器职责和共享工具参数的回归。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import AsyncMock, patch

from src.plugins.nonebot_plugin_fxbot.chat.replyer import generator
from src.plugins.nonebot_plugin_fxbot.chat.providers import LLMResponse
from src.plugins.nonebot_plugin_fxbot.chat.heart_flow import prompts
from src.plugins.nonebot_plugin_fxbot.chat.focus.tools import _prefix_local_refs, _control_definitions


class SourcePromptTests(unittest.TestCase):
    """复制的模板与参照源码相同，普通、被动与共享模式各用对应模板。"""

    def test_packaged_templates_equal_source(self) -> None:
        root = Path(__file__).resolve().parents[2]
        source = root / "temp/MaiBot/prompts/zh-CN"
        if not source.is_dir():
            self.skipTest("本地 MaiBot 对照源码未提供")
        for template in (root / "chat/prompts").glob("*.prompt"):
            if template.stem in {"maisaka_chat", "maisaka_chat_passive", "learn_style", "learn_jargon"}:
                # 宿主同时支持私聊与群聊，这些模板明确移除了上游的群聊场景假设。
                continue
            if template.name == "person_fact_writeback.prompt":
                code = (root / "temp/MaiBot/src/services/memory_flow_service.py").read_text(encoding="utf-8")
                original = code.split('prompt = f"""', 1)[1].split('"""', 1)[0]
                original = original.replace("{user_evidence_text}", "{evidence}").replace("{reply_text}", "{reply}")
                self.assertEqual(template.read_text(encoding="utf-8").strip(), original.strip())
                continue
            with self.subTest(template=template.name):
                self.assertEqual(
                    template.read_text(encoding="utf-8").strip(),
                    (source / template.name).read_text(encoding="utf-8").strip(),
                )

    def test_planner_uses_each_source_template(self) -> None:
        for passive, focus, marker in (
            (False, False, "发言必须通过reply工具"),
            (True, False, "set_reply_options()"),
            (False, True, "# Focus 共享思考"),
        ):
            with self.subTest(passive=passive, focus=focus):
                with patch.object(prompts, "planner_config", return_value={"passive_replyer": passive}):
                    text = prompts.planner_prompt("小鱼", "## 人格特征\n友好", {}, is_group=True, focus=focus)
                self.assertIn(marker, text)
                self.assertIn("小鱼", text)
                self.assertNotIn("{personality}", text)

    def test_focus_controls_require_valid_source_session(self) -> None:
        for definition in _control_definitions(["own"]):
            schema = definition["function"]["parameters"]
            self.assertIn("session_id", schema["required"])
            self.assertEqual(schema["properties"]["session_id"]["enum"], ["own"])

    def test_wrapped_schema_refs_remain_inside_business_arguments(self) -> None:
        schema = {"items": [{"$ref": "#/$defs/item"}], "$defs": {"item": {"type": "string"}}}
        result = _prefix_local_refs(schema, "#/properties/tool_arguments")
        self.assertEqual(result["items"][0]["$ref"], "#/properties/tool_arguments/$defs/item")
        self.assertEqual(schema["items"][0]["$ref"], "#/$defs/item")


class ReplyerRequestTests(unittest.IsolatedAsyncioTestCase):
    """回复模型不接收工具，采用上游任务参数。"""

    async def test_replyer_does_not_execute_or_advertise_tools(self) -> None:
        provider = SimpleNamespace(chat=AsyncMock(return_value=LLMResponse(content="你好")))
        messages = [{"role": "user", "content": "你好"}]
        result = await generator.generate(None, None, None, provider, messages, [{"name": "lookup"}], {"lookup"}, 0, 30)
        self.assertEqual(result.content, "你好")
        provider.chat.assert_awaited_once_with(messages=messages, tools=None, temperature=1.0, max_tokens=8192)

    async def test_unexpected_tool_call_is_not_sent_or_retried(self) -> None:
        provider = SimpleNamespace(chat=AsyncMock(return_value=LLMResponse(tool_calls=[{"id": "unexpected"}])))
        result = await generator.generate(None, None, None, provider, [], None, set(), 0, 30)
        self.assertFalse(result.content)
        self.assertFalse(result.has_tool_calls)
        self.assertEqual(provider.chat.await_count, 1)
