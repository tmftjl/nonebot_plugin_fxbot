"""回复器对「只有思考没有正文」响应的续写与重生成降级。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.plugins.nonebot_plugin_fxbot.chat.replyer import generator
from src.plugins.nonebot_plugin_fxbot.chat.providers import LLMResponse

MESSAGES = [{"role": "user", "content": "在吗"}]


class ReasoningFallbackTests(unittest.IsolatedAsyncioTestCase):
    """续写优先，不支持时提示正文重生成一次。"""

    async def test_continuation_result_is_used(self) -> None:
        continued = LLMResponse(content="在的")
        provider = SimpleNamespace(
            chat_continuation=AsyncMock(return_value=continued),
            chat=AsyncMock(side_effect=AssertionError("不应重生成")),
        )
        result = await generator._continue_reasoning(provider, MESSAGES, LLMResponse(reasoning_content="思考"))
        self.assertIs(result, continued)
        self.assertEqual(result.reasoning_content, "思考")
        self.assertEqual(provider.chat_continuation.await_args.kwargs["messages"], MESSAGES)

    async def test_continuation_tool_calls_are_discarded(self) -> None:
        continued = LLMResponse(
            content="在的",
            tool_calls=[{"id": "c", "function": {"name": "x"}}],
            tool_call_ids=["c"],
        )
        provider = SimpleNamespace(chat_continuation=AsyncMock(return_value=continued))
        result = await generator._continue_reasoning(provider, MESSAGES, LLMResponse(reasoning_content="思考"))
        self.assertEqual(result.content, "在的")
        self.assertEqual(result.tool_calls, [])
        self.assertEqual(result.tool_call_ids, [])

    async def test_empty_continuation_falls_back_to_hint_regeneration(self) -> None:
        retry = LLMResponse(content="在的")
        provider = SimpleNamespace(
            chat_continuation=AsyncMock(return_value=None),
            chat=AsyncMock(return_value=retry),
        )
        result = await generator._continue_reasoning(provider, MESSAGES, LLMResponse(reasoning_content="思考"))
        self.assertIs(result, retry)
        self.assertEqual(result.reasoning_content, "思考")
        sent = provider.chat.await_args.kwargs["messages"]
        self.assertEqual(sent[-1], {"role": "user", "content": generator.REASONING_ONLY_HINT})

    async def test_hint_regeneration_merges_into_initial_snapshot(self) -> None:
        previous = LLMResponse(reasoning_content="思考")
        previous._trace_key = "snapshot-key"
        provider = SimpleNamespace(
            chat_continuation=AsyncMock(return_value=None),
            chat=AsyncMock(return_value=LLMResponse(content="在的")),
        )
        await generator._continue_reasoning(provider, MESSAGES, previous)
        sent = provider.chat.await_args.kwargs
        self.assertEqual(sent["_trace_merge"], "snapshot-key")
        self.assertEqual(sent["_trace_purpose"], "replyer.reasoning_regeneration")
        self.assertEqual(sent["_trace_attempt"], 2)

    async def test_continuation_error_falls_back_to_hint_regeneration(self) -> None:
        retry = LLMResponse(content="在的")
        provider = SimpleNamespace(
            chat_continuation=AsyncMock(side_effect=RuntimeError("unsupported")),
            chat=AsyncMock(return_value=retry),
        )
        result = await generator._continue_reasoning(provider, MESSAGES, LLMResponse(reasoning_content="思考"))
        self.assertEqual(result.content, "在的")
        provider.chat.assert_awaited_once()

    async def test_tool_call_on_regeneration_is_discarded(self) -> None:
        provider = SimpleNamespace(
            chat_continuation=AsyncMock(return_value=None),
            chat=AsyncMock(return_value=LLMResponse(tool_calls=[{"id": "c", "function": {"name": "x"}}])),
        )
        result = await generator._continue_reasoning(provider, MESSAGES, LLMResponse(reasoning_content="思考"))
        self.assertEqual(result.content, "")
        self.assertEqual(result.tool_calls, [])
        self.assertEqual(result.reasoning_content, "思考")

    async def test_generate_only_continues_reasoning_only_response(self) -> None:
        provider = SimpleNamespace(
            chat=AsyncMock(return_value=LLMResponse(reasoning_content="思考")),
            chat_continuation=AsyncMock(return_value=LLMResponse(content="在的")),
        )
        result = await generator.generate(None, None, None, provider, MESSAGES, None, set(), 0, 0)
        self.assertEqual(result.content, "在的")
        provider.chat_continuation.assert_awaited_once()

    async def test_generate_keeps_normal_empty_response(self) -> None:
        provider = SimpleNamespace(chat=AsyncMock(return_value=LLMResponse()), chat_continuation=AsyncMock())
        result = await generator.generate(None, None, None, provider, MESSAGES, None, set(), 0, 0)
        self.assertEqual(result.content, "")
        provider.chat_continuation.assert_not_awaited()

    async def test_generate_continues_encrypted_reasoning_without_summary(self) -> None:
        response = LLMResponse(
            provider_data={
                "client_type": "openai_responses",
                "output_items": [{"type": "reasoning", "summary": [], "encrypted_content": "encrypted"}],
            }
        )
        provider = SimpleNamespace(
            chat=AsyncMock(return_value=response),
            chat_continuation=AsyncMock(return_value=LLMResponse(content="在的")),
        )
        result = await generator.generate(None, None, None, provider, MESSAGES, None, set(), 0, 0)
        self.assertEqual(result.content, "在的")
        self.assertIs(provider.chat_continuation.await_args.kwargs["previous"], response)

    async def test_generate_keeps_visible_response_with_native_reasoning(self) -> None:
        response = LLMResponse(
            content="在的",
            provider_data={"client_type": "openai_responses", "output_items": [{"type": "reasoning"}]},
        )
        provider = SimpleNamespace(chat=AsyncMock(return_value=response), chat_continuation=AsyncMock())
        result = await generator.generate(None, None, None, provider, MESSAGES, None, set(), 0, 0)
        self.assertIs(result, response)
        provider.chat_continuation.assert_not_awaited()
