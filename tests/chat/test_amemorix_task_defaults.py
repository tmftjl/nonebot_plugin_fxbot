"""长期记忆模型桥接使用 MaiBot 任务默认参数。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

from src.plugins.nonebot_plugin_fxbot.chat.amemorix._host import llm


class MemoryTaskDefaultsTests(unittest.IsolatedAsyncioTestCase):
    """验证真实桥接调用参数及显式零温度覆盖。"""

    async def test_task_defaults_and_zero_temperature_override_reach_provider(self) -> None:
        provider = SimpleNamespace(name="test", chat=AsyncMock(return_value=SimpleNamespace(content="[]")))

        @asynccontextmanager
        async def lease(*args):
            yield provider

        with patch.object(llm, "fallback_provider", lease):
            for task, temperature, tokens in (("utils", 0.5, 8192), ("memory", 0.3, 8192), ("planner", 0.7, 16384)):
                await llm.LLMServiceClient(task_name=task).generate_response("test")
                self.assertEqual(provider.chat.await_args.kwargs["temperature"], temperature)
                self.assertEqual(provider.chat.await_args.kwargs["max_tokens"], tokens)
            client = llm.LLMServiceClient(task_name="utils")
            client._orchestrator.model_for_task = llm.TaskConfig(temperature=0, max_tokens=512)
            await client.generate_response("test")
            self.assertEqual(provider.chat.await_args.kwargs["temperature"], 0)
            self.assertEqual(provider.chat.await_args.kwargs["max_tokens"], 512)
