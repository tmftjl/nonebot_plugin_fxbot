"""上下文预算：本轮正文截断与历史条数上限。"""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import AsyncMock, patch

from src.plugins.nonebot_plugin_fxbot.chat import service as service_module
from src.plugins.nonebot_plugin_fxbot.chat.types import ChatRequest
from src.plugins.nonebot_plugin_fxbot.chat.service import ChatService
from src.plugins.nonebot_plugin_fxbot.chat.session import ChatSessionStore
from src.plugins.nonebot_plugin_fxbot.chat.providers import LLMResponse
from src.plugins.nonebot_plugin_fxbot.chat.replyer.prompt import truncate_content as _truncate_content

from .support import ReplyPipelineTests

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
    """只提供对话上下文相关配置。"""

    def get_system(self) -> dict[str, Any]:
        return {
            "chat": {
                "max_context_size": 10,
                "persona": "",
                "provider": "",
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

    async def chat(self, messages: list[dict[str, Any]], tools: Any = None, **kwargs) -> LLMResponse:
        self.prompts.append(list(messages))
        return LLMResponse(content="收到")


def _contents(prompt: list[dict[str, Any]]) -> list[str]:
    """取出提示里的文本内容，忽略人格系统提示。"""
    return [str(message.get("content") or "") for message in prompt if message.get("role") != "system"]


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


class BudgetIntegrationTests(ReplyPipelineTests):
    """上下文上限在真实请求构造里生效。"""

    def setUp(self) -> None:
        self.store = ChatSessionStore()
        self.service = ChatService(self.store)

    async def _run(self, provider: _Provider, config: _Config, text: str = "在吗") -> None:
        with (
            patch.object(service_module, "get_config_manager", return_value=config),
            patch.object(service_module, "memory_block", new=AsyncMock(return_value="")),
            patch.object(ChatService, "_persist_turn_start", new=AsyncMock(return_value=None)),
            patch.object(service_module.memory_state, "is_ready", return_value=False),
        ):
            await self.service._process_with_provider(_request(text), None, provider, "provider")

    async def test_context_is_retained_until_cycle_boundary(self) -> None:
        for role, content in (
            ("user", "旧问题"),
            ("assistant", "旧回答"),
            ("user", "新问题"),
            ("assistant", "新回答"),
        ):
            self.store.append(
                TENANT,
                {"role": role, "content": content, "_sender_id": "user", "_self_message": role == "assistant"},
                limit=2,
            )
        provider = _Provider()

        await self._run(provider, _Config())

        contents = _contents(provider.prompts[0])
        self.assertIn("新问题", contents)
        self.assertIn("旧问题", contents)
        self.assertEqual(provider.calls, 1)

    async def test_current_message_is_truncated_but_history_keeps_full_text(self) -> None:
        provider = _Provider()

        await self._run(provider, _Config(), "啊" * 4500)

        current = next(text for text in _contents(provider.prompts[0]) if text.startswith("<message "))
        self.assertIn('user="40001"', current)
        current = current.split("\n", 1)[1]
        self.assertEqual(len(current), 4000 - len("用户40001: "))
        self.assertTrue(current.endswith("…"))
        stored = self.store.get(TENANT)[-1]["content"]
        self.assertGreater(len(stored), 4000)


if __name__ == "__main__":
    unittest.main()
