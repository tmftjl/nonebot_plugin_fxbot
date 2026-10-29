"""规划器流式工具参数拼接和连接关闭回归。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.plugins.nonebot_plugin_fxbot.chat.providers.sources.openai import OpenAIProvider


class PlannerStreamTests(unittest.IsolatedAsyncioTestCase):
    """使用协议片段验证真实 Provider 的流式工具解析。"""

    async def test_fragmented_tool_call_survives_stream(self) -> None:
        fragments = [
            SimpleNamespace(index=0, id="call-1", function=SimpleNamespace(name="reply", arguments='{"msg_id":')),
            SimpleNamespace(index=0, id=None, function=SimpleNamespace(name=None, arguments='"m1"}')),
        ]

        class Stream:
            """提供两个工具参数片段。"""

            close = AsyncMock()

            def __aiter__(self):
                return self.events()

            async def events(self):
                for fragment in fragments:
                    yield SimpleNamespace(
                        choices=[SimpleNamespace(delta=SimpleNamespace(content=None, tool_calls=[fragment]))]
                    )

        stream = Stream()
        create = AsyncMock(return_value=stream)
        provider = object.__new__(OpenAIProvider)
        provider.provider_id = "test"
        provider.model_name = "test"
        provider.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        messages = [{"role": "user", "content": "图片", "_tool_media": {"tool_result:1:1": "private-runtime-marker"}}]
        responses = [response async for response in provider.chat_stream(messages, tools=[], max_tokens=16384)]
        self.assertEqual(responses[-1].tool_calls[0]["function"], {"name": "reply", "arguments": '{"msg_id":"m1"}'})
        self.assertEqual(responses[-1].tool_call_ids, ["call-1"])
        self.assertEqual(create.await_args.kwargs["max_tokens"], 16384)
        self.assertEqual(create.await_args.kwargs["messages"], [{"role": "user", "content": "图片"}])
        self.assertIn("_tool_media", messages[0])
        stream.close.assert_awaited_once()
