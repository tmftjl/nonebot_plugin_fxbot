"""模型断句：model_routing.fast_model 配置后单独走快速模型。"""

from __future__ import annotations

import json
from typing import Any
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

from src.plugins.nonebot_plugin_fxbot.chat import service as service_module
from src.plugins.nonebot_plugin_fxbot.chat.types import ChatRequest
from src.plugins.nonebot_plugin_fxbot.chat.service import ChatService
from src.plugins.nonebot_plugin_fxbot.chat.session import ChatSessionStore
from src.plugins.nonebot_plugin_fxbot.chat.providers import LLMResponse, routing as routing_module

from .support import ReplyPipelineTests

TENANT = "test:splitter-routing"
TEXT = "第一句。第二句。"
SENTENCES = ["第一句。", "第二句。"]


def _request() -> ChatRequest:
    """构造一条私聊触发。"""
    return ChatRequest(
        session_id=TENANT,
        user_id="40001",
        group_id=None,
        text="在吗",
        turn_id="turn-splitter-routing",
        dedupe_key="msg:splitter-routing",
        trigger_kind="private",
    )


class _Config:
    """只提供路由与断句需要的配置。"""

    def __init__(self, fast_model: str) -> None:
        self.fast_model = fast_model

    def get_system(self) -> dict[str, Any]:
        return {
            "chat": {
                "max_context_size": 10,
                "persona": "",
                "provider": "",
                "model_routing": {"fast_model": self.fast_model},
                "response_splitter": {"mode": "llm", "enable": True},
            }
        }


class _Provider:
    """固定返回内容并记录调用次数。"""

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls = 0

    async def chat(self, messages: list[dict[str, Any]], tools: Any = None, **kwargs: Any) -> LLMResponse:
        self.calls += 1
        return LLMResponse(content=self.reply)


class SplitterRoutingTests(ReplyPipelineTests):
    """配置了快速模型时断句用它，否则沿用 utils。"""

    def setUp(self) -> None:
        self.service = ChatService(ChatSessionStore())

    async def _run(self, fast_model: str) -> tuple[_Provider, _Provider, list[str]]:
        reply = _Provider(TEXT)
        fast = _Provider(json.dumps(SENTENCES, ensure_ascii=False))
        leased: list[str] = []

        @asynccontextmanager
        async def _fake_fallback(task: str, session_id: str = ""):
            leased.append(task)
            yield fast

        config = _Config(fast_model)
        with (
            patch.object(service_module, "get_config_manager", return_value=config),
            patch.object(routing_module, "get_config_manager", return_value=config),
            patch.object(service_module, "fallback_provider", new=_fake_fallback),
            patch.object(service_module, "memory_block", new=AsyncMock(return_value="")),
            patch.object(ChatService, "_persist_turn_start", new=AsyncMock(return_value=None)),
            patch.object(service_module.memory_state, "is_ready", return_value=False),
        ):
            await self.service._process_with_provider(_request(), None, reply, "reply")
        return reply, fast, leased

    async def test_configured_fast_model_splits(self) -> None:
        reply, fast, leased = await self._run("fast-provider")

        self.assertEqual(leased, ["fast_model"])
        self.assertEqual(fast.calls, 1)
        self.assertEqual(reply.calls, 1)

    async def test_unconfigured_falls_back_to_utils_provider(self) -> None:
        reply, fast, leased = await self._run("")

        self.assertEqual(leased, ["utils"])
        self.assertEqual(fast.calls, 1)
        self.assertEqual(reply.calls, 1)
