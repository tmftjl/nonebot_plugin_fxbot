"""OpenAI Responses 通道的输入转换、输出解析与推理续写。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.plugins.nonebot_plugin_fxbot.chat.providers import LLMResponse
from src.plugins.nonebot_plugin_fxbot.chat.providers.sources.openai import _reasoning_of, _inline_reasoning
from src.plugins.nonebot_plugin_fxbot.chat.providers.sources.openai_responses import (
    OpenAIResponsesProvider,
    _input_items,
    _parse_response,
    _tool_definitions,
)


def _provider() -> OpenAIResponsesProvider:
    return OpenAIResponsesProvider(
        "responses",
        {"api_key": "test", "base_url": "http://127.0.0.1:9/v1", "model": "gpt-test", "timeout": 1},
    )


class ReasoningFieldTests(unittest.TestCase):
    """兼容接口的思考内容字段命名不一致，读取时依次回退。"""

    def test_reasoning_content_attribute(self) -> None:
        self.assertEqual(_reasoning_of(SimpleNamespace(reasoning_content="原生")), "原生")

    def test_reasoning_fallback(self) -> None:
        self.assertEqual(_reasoning_of(SimpleNamespace(reasoning="兼容")), "兼容")

    def test_model_extra_fields(self) -> None:
        message = SimpleNamespace(model_extra={"reasoning": "扩展"}, reasoning_content=None)
        self.assertEqual(_reasoning_of(message), "扩展")

    def test_absent(self) -> None:
        self.assertEqual(_reasoning_of(SimpleNamespace()), "")


class InlineReasoningTests(unittest.TestCase):
    """部分兼容模型把思考写进正文，内联块要拆出来。"""

    def test_closed_block_is_split(self) -> None:
        self.assertEqual(_inline_reasoning("<think>想一想</think>你好", ""), ("想一想", "你好"))

    def test_unclosed_block_becomes_reasoning_only(self) -> None:
        self.assertEqual(_inline_reasoning("<think>没写完", ""), ("没写完", ""))

    def test_plain_content_is_untouched(self) -> None:
        self.assertEqual(_inline_reasoning("你好", ""), ("", "你好"))

    def test_native_reasoning_wins(self) -> None:
        self.assertEqual(_inline_reasoning("<think>正文块</think>你好", "原生"), ("原生", "<think>正文块</think>你好"))


class ResponsesFormatTests(unittest.TestCase):
    """请求消息与工具定义按 Responses 原生形状下发。"""

    def test_input_items_cover_text_image_tools_and_outputs(self) -> None:
        items = _input_items(
            [
                {"role": "system", "content": "人格"},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "看图"},
                        {"type": "image_url", "image_url": {"url": "http://x/1.png"}},
                    ],
                },
                {
                    "role": "assistant",
                    "content": "我查一下",
                    "tool_calls": [{"id": "call-1", "function": {"name": "search", "arguments": '{"q": 1}'}}],
                },
                {"role": "tool", "tool_call_id": "call-1", "content": "结果"},
            ]
        )
        self.assertEqual(items[0], {"role": "system", "content": "人格"})
        self.assertEqual(
            items[1],
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "看图"},
                    {"type": "input_image", "image_url": "http://x/1.png", "detail": "auto"},
                ],
            },
        )
        self.assertEqual(
            items[2],
            {"type": "function_call", "call_id": "call-1", "name": "search", "arguments": '{"q": 1}'},
        )
        self.assertEqual(items[3], {"role": "assistant", "content": "我查一下"})
        self.assertEqual(
            items[4],
            {"type": "function_call_output", "call_id": "call-1", "output": "结果"},
        )

    def test_tool_definitions_are_flattened(self) -> None:
        tools = _tool_definitions(
            [
                {
                    "type": "function",
                    "function": {
                        "name": "search",
                        "description": "搜索",
                        "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
                    },
                },
                {"type": "web_search"},
            ]
        )
        self.assertEqual(
            tools,
            [
                {
                    "type": "function",
                    "name": "search",
                    "description": "搜索",
                    "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
                }
            ],
        )


class ResponsesParseTests(unittest.TestCase):
    """推理、正文与工具调用从 output 条目还原。"""

    def test_parse_completed_response(self) -> None:
        response = SimpleNamespace(
            status="completed",
            id="resp-1",
            output=[
                {"type": "reasoning", "summary": [{"type": "summary_text", "text": "先看问题"}]},
                {"type": "message", "content": [{"type": "output_text", "text": "答案是 42"}]},
                {
                    "type": "function_call",
                    "call_id": "call-9",
                    "name": "lookup",
                    "arguments": '{"id": 1}',
                },
            ],
        )
        parsed = _parse_response(response, "gpt-test")
        self.assertEqual(parsed.content, "答案是 42")
        self.assertEqual(parsed.reasoning_content, "先看问题")
        self.assertEqual(parsed.tool_call_ids, ["call-9"])
        self.assertEqual(parsed.tool_calls[0]["function"]["name"], "lookup")
        self.assertEqual(parsed.provider_data["client_type"], "openai_responses")
        self.assertEqual(
            [item["type"] for item in parsed.provider_data["output_items"]], ["reasoning", "message", "function_call"]
        )

    def test_parse_plain_reasoning_text_when_summary_missing(self) -> None:
        response = SimpleNamespace(
            status="completed",
            id="resp-2",
            output=[
                {"type": "reasoning", "summary": [], "content": [{"type": "reasoning_text", "text": "明文思考"}]},
                {"type": "message", "content": [{"type": "refusal", "refusal": "不能回答"}]},
            ],
        )
        parsed = _parse_response(response, "m")
        self.assertEqual(parsed.reasoning_content, "明文思考")
        self.assertEqual(parsed.content, "不能回答")

    def test_incomplete_status_raises(self) -> None:
        response = SimpleNamespace(status="incomplete", incomplete_details="max_output_tokens", output=[])
        with self.assertRaises(RuntimeError):
            _parse_response(response, "m")

    def test_tool_round_trip_preserves_complete_native_output(self) -> None:
        output = [
            {"type": "reasoning", "id": "rs-1", "summary": [], "encrypted_content": "encrypted"},
            {
                "type": "message",
                "id": "msg-1",
                "role": "assistant",
                "phase": "commentary",
                "content": [{"type": "output_text", "text": "我查一下"}],
            },
            {"type": "function_call", "id": "fc-1", "call_id": "c-1", "name": "lookup", "arguments": "{}"},
        ]
        parsed = _parse_response(SimpleNamespace(status="completed", output=output), "m")
        replay = _input_items([parsed.to_message_dict(), {"role": "tool", "tool_call_id": "c-1", "content": "结果"}])
        self.assertEqual(replay, [*output, {"type": "function_call_output", "call_id": "c-1", "output": "结果"}])


class ResponsesJsonTests(unittest.IsolatedAsyncioTestCase):
    """结构化输出走 Responses 原生 text.format。"""

    async def asyncSetUp(self) -> None:
        self.provider = _provider()
        self.addAsyncCleanup(self.provider.close)

    async def _create(self, text: str) -> AsyncMock:
        return AsyncMock(
            return_value=SimpleNamespace(
                status="completed",
                output=[{"type": "message", "content": [{"type": "output_text", "text": text}]}],
            )
        )

    async def test_chat_json_uses_json_schema_format(self) -> None:
        create = await self._create('{"ok": true}')
        schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}}
        with unittest.mock.patch.object(self.provider.client.responses, "create", create):
            result = await self.provider.chat_json([{"role": "user", "content": "hi"}], schema)
        self.assertEqual(result, {"ok": True})
        kwargs = create.await_args.kwargs
        self.assertFalse(kwargs["store"])
        self.assertEqual(kwargs["text"]["format"]["type"], "json_schema")
        self.assertEqual(kwargs["text"]["format"]["schema"], schema)
        self.assertTrue(kwargs["text"]["format"]["strict"])

    async def test_chat_json_without_schema_uses_json_object(self) -> None:
        create = await self._create("{}")
        with unittest.mock.patch.object(self.provider.client.responses, "create", create):
            result = await self.provider.chat_json([{"role": "user", "content": "hi"}])
        self.assertEqual(result, {})
        self.assertEqual(create.await_args.kwargs["text"]["format"], {"type": "json_object"})


class ResponsesContinuationTests(unittest.IsolatedAsyncioTestCase):
    """续写把上一轮原始输出条目追加回 input。"""

    async def asyncSetUp(self) -> None:
        self.provider = _provider()
        self.addAsyncCleanup(self.provider.close)

    async def test_continuation_replays_reasoning_items(self) -> None:
        sent: dict = {}

        async def fake_create(**params):
            sent.update(params)
            return LLMResponse(content="续写正文")

        self.provider._create = fake_create  # type: ignore[method-assign]
        output_items = [
            {"type": "reasoning", "id": "rs-1", "summary": [{"type": "summary_text", "text": "思考"}]},
            {"type": "message", "content": [{"type": "output_text", "text": ""}]},
        ]
        previous = LLMResponse(
            reasoning_content="思考",
            provider_data={"client_type": "openai_responses", "model": "gpt-test", "output_items": output_items},
        )
        result = await self.provider.chat_continuation(
            messages=[{"role": "user", "content": "继续"}], previous=previous, temperature=1.0, max_tokens=100
        )
        assert result is not None
        self.assertEqual(result.content, "续写正文")
        self.assertEqual(sent["input_items"][-2:], output_items)
        self.assertEqual(sent["max_tokens"], 100)

    async def test_continuation_skips_without_reasoning_items(self) -> None:
        previous = LLMResponse(provider_data={"client_type": "openai_responses", "output_items": [{"type": "message"}]})
        result = await self.provider.chat_continuation(messages=[], previous=previous)
        self.assertIsNone(result)

    async def test_chat_sends_stateless_request(self) -> None:
        create = AsyncMock(
            return_value=SimpleNamespace(
                status="completed",
                id="resp-3",
                output=[{"type": "message", "content": [{"type": "output_text", "text": "hi"}]}],
            )
        )
        with unittest.mock.patch.object(self.provider.client.responses, "create", create):
            response = await self.provider.chat([{"role": "user", "content": "hi"}], temperature=0.5)
        self.assertEqual(response.content, "hi")
        kwargs = create.await_args.kwargs
        self.assertFalse(kwargs["store"])
        self.assertEqual(kwargs["temperature"], 0.5)
        self.assertNotIn("tools", kwargs)
