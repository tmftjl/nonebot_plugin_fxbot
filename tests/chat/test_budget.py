"""P1.5 上下文预算：历史整轮裁剪与本轮正文截断。"""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import AsyncMock, patch

from src.plugins.nonebot_plugin_fxbot.chat import service as service_module
from src.plugins.nonebot_plugin_fxbot.chat.types import ChatRequest
from src.plugins.nonebot_plugin_fxbot.chat.service import (
    ChatService,
    _trim_history,
    _history_budget,
    _truncate_content,
)
from src.plugins.nonebot_plugin_fxbot.chat.session import ChatSessionStore
from src.plugins.nonebot_plugin_fxbot.chat.providers import LLMResponse

TENANT = "test:chat-budget"


def _request(text: str = "在吗") -> ChatRequest:
    """构造一条私聊触发。"""
    return ChatRequest(
        session_id=TENANT,
        user_id="40001",
        group_id=None,
        text=text,
        turn_id="turn-budget",
        dedupe_key="msg:budget",
        trigger_kind="private",
    )


class _Config:
    """只提供对话预算相关配置。"""

    def __init__(self, history_chars: int) -> None:
        self.history_chars = history_chars

    def get_system(self) -> dict[str, Any]:
        return {
            "chat": {
                "max_history": 10,
                "image_description": False,
                "persona": "",
                "provider": "",
                "enabled_tools": ["__disabled__"],
                "memory": {"history_enabled": True, "max_history_chars": self.history_chars},
            }
        }


class _Provider:
    """记录提示并返回固定回复。"""

    def __init__(self) -> None:
        self.prompts: list[list[dict[str, Any]]] = []

    @property
    def calls(self) -> int:
        """已生成次数。"""
        return len(self.prompts)

    async def chat(self, messages: list[dict[str, Any]], tools: Any = None) -> LLMResponse:
        self.prompts.append(list(messages))
        return LLMResponse(content="收到")


def _contents(prompt: list[dict[str, Any]]) -> list[str]:
    """取出提示里的文本内容，忽略人格系统提示。"""
    return [str(message.get("content") or "") for message in prompt if message.get("role") != "system"]


class TrimHistoryTests(unittest.TestCase):
    """历史按整轮丢弃，从不拆开工具调用组。"""

    def test_oldest_group_is_dropped_first(self) -> None:
        history = [
            {"role": "user", "content": "旧问题"},
            {"role": "assistant", "content": "旧回答"},
            {"role": "user", "content": "新问题"},
            {"role": "assistant", "content": "新回答"},
        ]

        self.assertEqual(_trim_history(history, 4), history[2:])

    def test_tool_group_is_never_split(self) -> None:
        tool_group = [
            {"role": "user", "content": "查天气"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}]},
            {"role": "tool", "tool_call_id": "c1", "content": "晴"},
            {"role": "assistant", "content": "今天晴"},
        ]
        older = [{"role": "user", "content": "旧" * 8}, {"role": "assistant", "content": "答" * 8}]

        self.assertEqual(_trim_history([*older, *tool_group], 10), tool_group)

    def test_only_group_is_kept_even_over_budget(self) -> None:
        history = [{"role": "user", "content": "长" * 100}]

        self.assertEqual(_trim_history(history, 5), history)

    def test_zero_budget_disables_trimming(self) -> None:
        history = [{"role": "user", "content": "问"}, {"role": "assistant", "content": "答"}]

        self.assertEqual(_trim_history(history, 0), history)

    def test_image_parts_do_not_count_as_text(self) -> None:
        history = [
            {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "u"}}]},
            {"role": "assistant", "content": "看到了"},
        ]

        self.assertEqual(_trim_history(history, 2), history)


class TruncateContentTests(unittest.TestCase):
    """只截断本轮正文。"""

    def test_text_is_truncated_to_limit(self) -> None:
        self.assertEqual(_truncate_content("啊" * 11, 10), "啊" * 9 + "…")
        self.assertEqual(_truncate_content("啊" * 10, 10), "啊" * 10)

    def test_multimodal_text_is_cut_but_image_kept(self) -> None:
        content = [
            {"type": "text", "text": "a" * 8},
            {"type": "image_url", "image_url": {"url": "u"}},
            {"type": "text", "text": "b" * 8},
        ]

        result = _truncate_content(content, 10)

        self.assertEqual(result[0]["text"], "a" * 8)
        self.assertEqual(result[1], content[1])
        self.assertEqual(result[2]["text"], "b" + "…")

    def test_image_is_kept_after_text_budget_is_used(self) -> None:
        content = [
            {"type": "text", "text": "a" * 10},
            {"type": "image_url", "image_url": {"url": "u"}},
        ]

        self.assertEqual(_truncate_content(content, 10), content)


class HistoryBudgetTests(unittest.TestCase):
    """预算读取要容忍缺省和非法值。"""

    def test_defaults_when_missing(self) -> None:
        self.assertEqual(_history_budget({}), 12000)
        self.assertEqual(_history_budget({"memory": {}}), 12000)

    def test_non_negative_int_is_used(self) -> None:
        self.assertEqual(_history_budget({"memory": {"max_history_chars": 500}}), 500)
        self.assertEqual(_history_budget({"memory": {"max_history_chars": -3}}), 0)

    def test_invalid_types_fall_back(self) -> None:
        self.assertEqual(_history_budget({"memory": {"max_history_chars": True}}), 12000)
        self.assertEqual(_history_budget({"memory": {"max_history_chars": "500"}}), 12000)


class BudgetIntegrationTests(unittest.IsolatedAsyncioTestCase):
    """预算在真实请求构造里生效。"""

    def setUp(self) -> None:
        self.store = ChatSessionStore()
        self.service = ChatService(self.store)

    async def _run(self, provider: _Provider, config: _Config, text: str = "在吗") -> None:
        with (
            patch.object(service_module, "get_config_manager", return_value=config),
            patch.object(service_module, "memory_context", new=AsyncMock(return_value="")),
            patch.object(ChatService, "_persist_turn_start", new=AsyncMock(return_value=None)),
            patch.object(service_module.memory_state, "is_ready", return_value=False),
        ):
            await self.service._process_with_provider(_request(text), None, provider, "provider")

    async def test_old_history_is_trimmed_before_the_prompt(self) -> None:
        for role, content in (
            ("user", "旧" * 110),
            ("assistant", "答" * 110),
            ("user", "新问题"),
            ("assistant", "新回答"),
        ):
            self.store.append(TENANT, {"role": role, "content": content}, limit=10)
        provider = _Provider()

        await self._run(provider, _Config(history_chars=150))

        contents = _contents(provider.prompts[0])
        self.assertIn("新问题", contents)
        self.assertIn("新回答", contents)
        self.assertNotIn("旧" * 110, contents)
        self.assertEqual(provider.calls, 1)

    async def test_current_message_is_truncated_but_history_keeps_full_text(self) -> None:
        provider = _Provider()

        await self._run(provider, _Config(history_chars=12000), "啊" * 4500)

        current = _contents(provider.prompts[0])[-1]
        self.assertTrue(current.startswith("用户40001: "))
        self.assertEqual(len(current), 4000)
        self.assertTrue(current.endswith("…"))
        stored = self.store.get(TENANT)[-1]["content"]
        self.assertGreater(len(stored), 4000)


if __name__ == "__main__":
    unittest.main()
