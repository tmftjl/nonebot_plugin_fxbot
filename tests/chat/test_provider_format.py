"""各 Provider 的消息格式转换：多条系统消息按顺序合并，不互相覆盖。"""

from __future__ import annotations

import sys
import types
import unittest
import importlib.util
from unittest.mock import AsyncMock

if importlib.util.find_spec("anthropic") is None:
    # 只是验证转换函数，不需要真实 SDK
    sys.modules["anthropic"] = types.SimpleNamespace(AsyncAnthropic=object)

from src.plugins.nonebot_plugin_fxbot.chat.providers.base import local_http_client
from src.plugins.nonebot_plugin_fxbot.chat.providers.entities import LLMResponse
from src.plugins.nonebot_plugin_fxbot.chat.providers.sources.vertex import VertexAIProvider
from src.plugins.nonebot_plugin_fxbot.chat.providers.sources.anthropic import AnthropicProvider

MESSAGES = [
    {"role": "system", "content": "人格：猫娘"},
    {"role": "system", "content": "记忆：用户喜欢咖啡"},
    {"role": "system", "content": ""},
    {"role": "system", "content": "摘要：上一段聊了天气"},
    {"role": "user", "content": "你好"},
]
EXPECTED = "人格：猫娘\n\n记忆：用户喜欢咖啡\n\n摘要：上一段聊了天气"


class SystemMergeTests(unittest.TestCase):
    """人格、记忆、摘要必须一起送达。"""

    def test_anthropic(self) -> None:
        system, converted = object.__new__(AnthropicProvider)._convert_to_anthropic_format(MESSAGES)
        self.assertEqual(system, EXPECTED)
        self.assertEqual(converted, [{"role": "user", "content": "你好"}])

    def test_vertex(self) -> None:
        payload = object.__new__(VertexAIProvider)._convert_to_vertex_format(MESSAGES)
        self.assertEqual(payload["systemInstruction"], {"parts": [{"text": EXPECTED}]})
        self.assertEqual(len(payload["contents"]), 1)

    def test_no_system_message(self) -> None:
        messages = [{"role": "user", "content": "你好"}]
        self.assertEqual(object.__new__(AnthropicProvider)._convert_to_anthropic_format(messages)[0], "")
        self.assertNotIn("systemInstruction", object.__new__(VertexAIProvider)._convert_to_vertex_format(messages))


class LocalProxyBypassTests(unittest.IsolatedAsyncioTestCase):
    """局域网中转站不能被系统代理接管，否则只会收到 502。"""

    async def test_lan_base_url_bypasses_proxy(self) -> None:
        client = local_http_client("http://192.168.7.27:19997/api/v1", 30)
        self.assertIsNotNone(client)
        self.assertFalse(client._trust_env)
        await client.aclose()

    async def test_public_base_url_keeps_system_proxy(self) -> None:
        self.assertIsNone(local_http_client("https://api.deepseek.com", 30))
        self.assertIsNone(local_http_client("https://relay.example.com/v1", 30))


class ProviderRoundTripTests(unittest.TestCase):
    """响应 → 消息 → 再次请求时保留原生工具调用块。"""

    def test_vertex_native_parts_survive(self) -> None:
        native = [{"functionCall": {"name": "search", "args": {"q": "天气"}, "id": "c1"}}]
        response = LLMResponse(
            role="assistant",
            tool_calls=[{"id": "c1", "type": "function", "function": {"name": "search", "arguments": "{}"}}],
            provider_data={"vertex_parts": native},
        )

        payload = object.__new__(VertexAIProvider)._convert_to_vertex_format([response.to_message_dict()])

        self.assertEqual(payload["contents"], [{"role": "model", "parts": native}])

    def test_message_without_provider_data_is_plain(self) -> None:
        self.assertEqual(LLMResponse(content="你好").to_message_dict(), {"role": "assistant", "content": "你好"})


class AnthropicResponseTests(unittest.IsolatedAsyncioTestCase):
    """文本块按序拼接且工具调用保持完整。"""

    async def test_text_tool_text_response(self) -> None:
        """工具块前后的文本均保留且不额外插入分隔符。"""
        provider = object.__new__(AnthropicProvider)
        provider.model_name = "test-model"
        provider.client = types.SimpleNamespace(
            messages=types.SimpleNamespace(
                create=AsyncMock(
                    return_value=types.SimpleNamespace(
                        content=[
                            types.SimpleNamespace(type="text", text="先查询"),
                            types.SimpleNamespace(type="tool_use", id="c1", name="search", input={"q": "weather"}),
                            types.SimpleNamespace(type="text", text="再总结"),
                        ]
                    )
                )
            )
        )

        response = await provider.chat([{"role": "user", "content": "你好"}])

        self.assertEqual(response.content, "先查询再总结")
        self.assertEqual(
            response.tool_calls,
            [{"id": "c1", "type": "function", "function": {"name": "search", "arguments": '{"q": "weather"}'}}],
        )
        self.assertEqual(response.tool_call_ids, ["c1"])
