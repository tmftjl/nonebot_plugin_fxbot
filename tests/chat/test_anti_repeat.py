"""P1.5 防复读：相似度、连续开场词、只登记已发送回复。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from src.plugins.nonebot_plugin_fxbot.chat import service as service_module
from src.plugins.nonebot_plugin_fxbot.chat.tools import ToolRuntime
from src.plugins.nonebot_plugin_fxbot.chat.types import ChatRequest, ChatResponse
from src.plugins.nonebot_plugin_fxbot.chat.service import ChatService
from src.plugins.nonebot_plugin_fxbot.chat.session import ChatSessionStore
from src.plugins.nonebot_plugin_fxbot.chat.behavior import anti_repeat
from src.plugins.nonebot_plugin_fxbot.chat.providers import LLMResponse

TENANT = "test:anti-repeat"


def _request() -> ChatRequest:
    """构造一条私聊触发。"""
    return ChatRequest(
        session_id=TENANT,
        user_id="40001",
        group_id=None,
        text="在吗",
        turn_id="turn-anti-repeat",
        dedupe_key="msg:anti-repeat",
        trigger_kind="private",
    )


class _Config:
    """只提供防复读与对话所需的配置。"""

    def __init__(self, *, enabled: bool = True, threshold: int = 85, history_chars: int = 12000) -> None:
        self.enabled = enabled
        self.threshold = threshold
        self.history_chars = history_chars

    def get_system(self) -> dict[str, Any]:
        return {
            "chat": {
                "max_history": 10,
                "image_description": False,
                "persona": "",
                "provider": "",
                "enabled_tools": ["__disabled__"],
                "behavior": {"anti_repeat": self.enabled, "anti_repeat_threshold": self.threshold},
                "memory": {"history_enabled": True, "max_history_chars": self.history_chars},
            }
        }


class _SeqProvider:
    """按顺序返回回复并记录提示。"""

    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.prompts: list[list[dict[str, Any]]] = []

    @property
    def calls(self) -> int:
        """已生成次数。"""
        return len(self.prompts)

    async def chat(self, messages: list[dict[str, Any]], tools: Any = None) -> LLMResponse:
        self.prompts.append(list(messages))
        return LLMResponse(content=self.replies[min(self.calls - 1, len(self.replies) - 1)])


class DetectionTests(unittest.TestCase):
    """防复读判定本身。"""

    def setUp(self) -> None:
        anti_repeat.forget_replies(TENANT)

    def tearDown(self) -> None:
        anti_repeat.forget_replies(TENANT)

    def _config(self, *, enabled: bool = True, threshold: int = 85) -> Any:
        config = _Config(enabled=enabled, threshold=threshold)
        return patch.object(anti_repeat, "get_config_manager", return_value=config)

    def test_similar_reply_is_repeat(self) -> None:
        anti_repeat.remember_reply(TENANT, "今天天气真不错呀，我们出去走走吧")
        with self._config():
            self.assertEqual(anti_repeat.rewrite_reason(TENANT, "今天天气真不错呀，我们出去走走吧"), "repeat")
            self.assertEqual(anti_repeat.rewrite_reason(TENANT, "这个话题我不太熟悉，换个问题吧"), "")

    def test_threshold_config_is_respected(self) -> None:
        anti_repeat.remember_reply(TENANT, "今天天气真不错")
        with self._config(threshold=100):
            self.assertEqual(anti_repeat.rewrite_reason(TENANT, "今天天气真不错呀"), "")
        with self._config(enabled=False):
            self.assertEqual(anti_repeat.rewrite_reason(TENANT, "今天天气真不错"), "")

    def test_three_same_openers_force_rewrite(self) -> None:
        anti_repeat.remember_reply(TENANT, "你好呀，今天过得怎么样")
        anti_repeat.remember_reply(TENANT, "你好呀，晚上吃什么")
        with self._config():
            self.assertEqual(anti_repeat.rewrite_reason(TENANT, "你好呀，明天见"), "opener")

    def test_only_last_five_replies_are_kept(self) -> None:
        anti_repeat.remember_reply(TENANT, "很久以前说过的一句话")
        for index in range(5):
            anti_repeat.remember_reply(TENANT, f"最近第{index}条不同的回复")
        self.assertEqual(len(anti_repeat.recent_replies(TENANT)), 5)
        with self._config():
            self.assertEqual(anti_repeat.rewrite_reason(TENANT, "很久以前说过的一句话"), "")


class ServiceRewriteTests(unittest.IsolatedAsyncioTestCase):
    """生成的回复命中时只重写一次。"""

    def setUp(self) -> None:
        anti_repeat.forget_replies(TENANT)
        self.service = ChatService(ChatSessionStore())

    def tearDown(self) -> None:
        anti_repeat.forget_replies(TENANT)

    async def _run(self, provider: _SeqProvider, config: _Config) -> str:
        with (
            patch.object(service_module, "get_config_manager", return_value=config),
            patch.object(anti_repeat, "get_config_manager", return_value=config),
            patch.object(service_module, "memory_context", new=AsyncMock(return_value="")),
            patch.object(ChatService, "_persist_turn_start", new=AsyncMock(return_value=None)),
            patch.object(service_module.memory_state, "is_ready", return_value=False),
        ):
            response = await self.service._process_with_provider(_request(), None, provider, "provider")
        return response.text

    async def test_repeat_is_rewritten_once(self) -> None:
        repeated = "今天天气真不错呀，我们出去走走吧"
        anti_repeat.remember_reply(TENANT, repeated)
        provider = _SeqProvider(repeated, "换个说法，我们去公园转一圈吧。")

        text = await self._run(provider, _Config())

        self.assertEqual(text, "换个说法，我们去公园转一圈吧。")
        self.assertEqual(provider.calls, 2)
        self.assertIn("换个说法", provider.prompts[-1][-1]["content"])

    async def test_still_repeated_keeps_original(self) -> None:
        repeated = "今天天气真不错呀，我们出去走走吧"
        anti_repeat.remember_reply(TENANT, repeated)
        provider = _SeqProvider(repeated, repeated)

        text = await self._run(provider, _Config())

        self.assertEqual(text, repeated)
        self.assertEqual(provider.calls, 2)
        self.assertEqual(anti_repeat.recent_replies(TENANT), [repeated])

    async def test_disabled_does_not_generate_twice(self) -> None:
        repeated = "今天天气真不错呀，我们出去走走吧"
        anti_repeat.remember_reply(TENANT, repeated)
        provider = _SeqProvider(repeated)

        text = await self._run(provider, _Config(enabled=False))

        self.assertEqual(text, repeated)
        self.assertEqual(provider.calls, 1)


class SendRecordingTests(unittest.IsolatedAsyncioTestCase):
    """只有真正发出的分片才进入防复读缓存。"""

    def setUp(self) -> None:
        anti_repeat.forget_replies(TENANT)
        self.service = ChatService(ChatSessionStore())

    def tearDown(self) -> None:
        anti_repeat.forget_replies(TENANT)

    def _runtime(self, matcher: Any) -> ToolRuntime:
        return ToolRuntime(matcher=matcher)

    async def test_successful_send_is_recorded(self) -> None:
        matcher = SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(message_id="m-1")))
        request = _request()
        response = ChatResponse(text="已经发出的回复")
        with (
            patch.object(ChatService, "mark_sending", new=AsyncMock()),
            patch.object(ChatService, "mark_sent", new=AsyncMock()),
        ):
            self.assertTrue(await self.service._send_parts(request, response, self._runtime(matcher), matcher))

        self.assertEqual(anti_repeat.recent_replies(TENANT), ["已经发出的回复"])

    async def test_failed_send_is_not_recorded(self) -> None:
        matcher = SimpleNamespace(send=AsyncMock(side_effect=RuntimeError("send failed")))
        request = _request()
        response = ChatResponse(text="没有发出去的回复")
        with (
            patch.object(ChatService, "mark_sending", new=AsyncMock()),
            patch.object(ChatService, "mark_send_failed", new=AsyncMock()),
        ):
            self.assertFalse(await self.service._send_parts(request, response, self._runtime(matcher), matcher))

        self.assertEqual(anti_repeat.recent_replies(TENANT), [])


if __name__ == "__main__":
    unittest.main()
