"""模型响应契约、用量归一化与任务汇总回归。"""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

from src.plugins.nonebot_plugin_fxbot.chat.providers import routing
from src.plugins.nonebot_plugin_fxbot.chat.providers.base import ChatProvider
from src.plugins.nonebot_plugin_fxbot.chat.providers.entities import (
    TokenUsage,
    LLMResponse,
    JSONResponse,
    LLMResponseError,
    JSONResponseError,
    token_usage_of,
)
from src.plugins.nonebot_plugin_fxbot.chat.providers.sources.openai import OpenAIProvider
from src.plugins.nonebot_plugin_fxbot.chat.providers.sources.vertex import VertexAIProvider
from src.plugins.nonebot_plugin_fxbot.chat.providers.sources.openai_responses import OpenAIResponsesProvider

try:
    from src.plugins.nonebot_plugin_fxbot.chat.providers.sources.gemini import GeminiProvider
except ModuleNotFoundError as exc:
    if exc.name not in {"google", "google.genai"}:
        raise
    GeminiProvider = None

try:
    from src.plugins.nonebot_plugin_fxbot.chat.providers.sources.anthropic import AnthropicProvider
except ModuleNotFoundError as exc:
    if exc.name != "anthropic":
        raise
    AnthropicProvider = None


class ResponseTests(unittest.TestCase):
    """完整响应不会在 JSON 解析和异常路径中丢失。"""

    def test_unknown_and_explicit_zero_are_distinct(self) -> None:
        self.assertEqual(token_usage_of(None).to_dict(), {})
        self.assertEqual(token_usage_of({"usage": {"prompt_tokens": 0}}).to_dict(), {"prompt_tokens": 0})
        self.assertEqual(token_usage_of({"usage": {"prompt_tokens": True, "total_tokens": -1}}).to_dict(), {})

    def test_explicit_usage_is_not_reparsed(self) -> None:
        response = LLMResponse(raw_response={"usage": {"total_tokens": 100}}, usage=TokenUsage(total_tokens=0))
        self.assertEqual(response.usage.to_dict(), {"total_tokens": 0})

    def test_json_error_preserves_response_and_usage(self) -> None:
        for text in ("not json", "[]", "null", "", "1"):
            with self.subTest(text=text):
                response = LLMResponse(content=text, usage=TokenUsage(10, 5, 15))
                with self.assertRaises(JSONResponseError) as caught:
                    JSONResponse.from_response(response)
                self.assertIs(caught.exception.response, response)
                self.assertEqual(caught.exception.response.usage.total_tokens, 15)

    def test_fenced_json_preserves_original_content(self) -> None:
        response = LLMResponse(content='```json\n{"ok": true}\n```')
        result = JSONResponse.from_response(response)
        self.assertEqual(result.data, {"ok": True})
        self.assertIs(result.response, response)
        self.assertTrue(response.content.startswith("```json"))


class ProviderJSONTests(unittest.IsolatedAsyncioTestCase):
    """所有 Provider 统一返回数据与完整响应，解析错误也保留用量。"""

    async def test_all_json_providers_preserve_response(self) -> None:
        for kind in (OpenAIProvider, OpenAIResponsesProvider, GeminiProvider, VertexAIProvider, AnthropicProvider):
            if kind is None:
                with self.subTest(provider="optional SDK"):
                    self.skipTest("未安装可选 Provider SDK")
                continue
            for text in ('{"ok": true}', "not json"):
                with self.subTest(provider=kind.__name__, text=text):
                    provider = object.__new__(kind)
                    provider.provider_id = "test"
                    provider.model_name = "test-model"
                    provider.api_key = "test"
                    provider.base_url = "https://example.invalid"
                    provider.timeout = 1
                    usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
                    if kind is OpenAIProvider:
                        raw = SimpleNamespace(
                            usage=usage, choices=[SimpleNamespace(message=SimpleNamespace(content=text))]
                        )
                        provider.client = SimpleNamespace(
                            chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(return_value=raw)))
                        )
                    elif kind is OpenAIResponsesProvider:
                        raw = SimpleNamespace(
                            status="completed",
                            usage=usage,
                            output=[{"type": "message", "content": [{"type": "output_text", "text": text}]}],
                        )
                        provider.client = SimpleNamespace(responses=SimpleNamespace(create=AsyncMock(return_value=raw)))
                    elif kind is GeminiProvider:
                        raw = SimpleNamespace(
                            usage_metadata=usage,
                            candidates=[SimpleNamespace(content=SimpleNamespace(parts=[SimpleNamespace(text=text)]))],
                        )
                        provider.client = SimpleNamespace(
                            models=SimpleNamespace(generate_content=AsyncMock(return_value=raw))
                        )
                    elif kind is VertexAIProvider:
                        raw = {"usage": usage, "candidates": [{"content": {"parts": [{"text": text}]}}]}
                    else:
                        raw = SimpleNamespace(usage=usage)
                        provider.chat = AsyncMock(return_value=LLMResponse(content=text, raw_response=raw))
                    http = SimpleNamespace(
                        post=AsyncMock(return_value=SimpleNamespace(raise_for_status=lambda: None, json=lambda: raw))
                    )
                    with patch(
                        "src.plugins.nonebot_plugin_fxbot.chat.providers.sources.vertex.get_shared_async_client",
                        AsyncMock(return_value=http),
                    ):
                        if text == "not json":
                            with self.assertRaises(JSONResponseError) as caught:
                                await provider.chat_json([])
                            response = caught.exception.response
                        else:
                            result = await provider.chat_json([])
                            self.assertEqual(result.data, {"ok": True})
                            response = result.response
                    self.assertIs(response.raw_response, raw)
                    self.assertEqual(response.content, text)
                    self.assertEqual(response.usage.to_dict(), usage)

    async def test_base_json_uses_complete_chat_response(self) -> None:
        response = LLMResponse(content="{}", usage=TokenUsage(1, 2, 3))
        provider = SimpleNamespace(chat=AsyncMock(return_value=response))
        result = await ChatProvider.chat_json(provider, [])
        self.assertIs(result.response, response)


class TaskUsageTests(unittest.IsolatedAsyncioTestCase):
    """并发、降级、续写和流式结果只统计一次。"""

    async def asyncSetUp(self) -> None:
        self.provider = routing.TaskProvider(["test"], task="utils")
        self.provider._provider = SimpleNamespace(provider_id="test", get_model=lambda: "model")
        stats = patch.object(routing, "track_model_task_usage", _no_stats)
        stats.start()
        self.addCleanup(stats.stop)
        trace = patch.object(routing, "record_model_call", return_value="")
        self.record = trace.start()
        self.addCleanup(trace.stop)

    async def test_concurrent_json_calls_are_counted_once(self) -> None:
        async def generate(*, messages):
            count = messages[0]["count"]
            await asyncio.sleep(0)
            return JSONResponse.from_response(LLMResponse(content="{}", usage=TokenUsage(count, 1, count + 1)))

        self.provider._provider.chat_json = generate
        results = await asyncio.gather(*(self.provider.chat_json(messages=[{"count": n}]) for n in (1, 2, 3, 4)))
        self.assertEqual(len(results), 4)
        self.assertEqual(
            self.provider.usage.to_dict(), {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14}
        )

    async def test_json_failure_and_plain_fallback_both_count(self) -> None:
        invalid = LLMResponse(content="invalid", usage=TokenUsage(10, 2, 12))
        self.provider._provider.chat_json = AsyncMock(side_effect=JSONResponseError(invalid))
        self.provider._provider.chat = AsyncMock(return_value=LLMResponse(content="ok", usage=TokenUsage(20, 3, 23)))
        with self.assertRaises(JSONResponseError):
            await self.provider.chat_json(messages=[])
        self.assertIs(self.record.call_args.kwargs["response"], invalid)
        self.assertTrue(self.record.call_args.kwargs["error"])
        await self.provider.chat(messages=[])
        self.assertEqual(self.provider.usage.total_tokens, 35)

    async def test_continuation_and_final_stream_count_once(self) -> None:
        self.provider._provider.chat_continuation = AsyncMock(return_value=LLMResponse(usage=TokenUsage(1, 2, 3)))

        async def stream(**kwargs):
            yield LLMResponse(content="part", is_stream_chunk=True, usage=TokenUsage(100, 100, 200))
            yield LLMResponse(content="part", usage=TokenUsage(4, 5, 9))

        self.provider._provider.chat_stream = stream
        await self.provider.chat_continuation(messages=[], previous=LLMResponse())
        async for _ in self.provider.chat_stream(messages=[]):
            pass
        self.assertEqual(
            self.provider.usage.to_dict(), {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12}
        )

    async def test_network_error_does_not_invent_usage(self) -> None:
        self.provider._provider.chat = AsyncMock(side_effect=TimeoutError)
        with self.assertRaises(TimeoutError):
            await self.provider.chat(messages=[])
        self.assertEqual(self.provider.usage.to_dict(), {})

    async def test_unusable_response_usage_is_preserved_for_each_entry_point(self) -> None:
        for method in ("chat", "chat_json", "chat_continuation", "chat_stream"):
            with self.subTest(method=method):
                self.provider.usage = TokenUsage()
                response = LLMResponse(content="partial", usage=TokenUsage(10, 2, 12))
                error = LLMResponseError(response, "生成未完成")
                if method == "chat_stream":

                    async def stream(**kwargs):
                        yield LLMResponse(content="partial", is_stream_chunk=True)
                        raise error

                    self.provider._provider.chat_stream = stream
                else:
                    setattr(self.provider._provider, method, AsyncMock(side_effect=error))
                with self.assertRaises(LLMResponseError):
                    if method == "chat_stream":
                        async for _ in self.provider.chat_stream(messages=[]):
                            pass
                    elif method == "chat_continuation":
                        await self.provider.chat_continuation(messages=[], previous=LLMResponse())
                    else:
                        await getattr(self.provider, method)(messages=[])
                self.assertEqual(self.provider.usage.total_tokens, 12)
                self.assertIs(self.record.call_args.kwargs["response"], response)


class StreamResponseTests(unittest.IsolatedAsyncioTestCase):
    """流式用量来自服务端最终快照，不把累计快照重复相加。"""

    async def test_openai_preserves_usage_only_chunk_and_closes_stream(self) -> None:
        usage = SimpleNamespace(prompt_tokens=4, completion_tokens=2, total_tokens=6)
        final = SimpleNamespace(usage=usage, choices=[])

        async def chunks():
            yield SimpleNamespace(
                usage=None, choices=[SimpleNamespace(delta=SimpleNamespace(content="ok", tool_calls=[]))]
            )
            yield final

        iterator = chunks()
        stream = SimpleNamespace(close=AsyncMock())

        class Stream:
            """模拟 SDK 的异步流关闭接口。"""

            def __aiter__(self):
                return iterator

            async def close(self):
                await stream.close()

        create = AsyncMock(return_value=Stream())
        provider = object.__new__(OpenAIProvider)
        provider.provider_id = "test"
        provider.model_name = "model"
        provider.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        responses = [response async for response in provider.chat_stream([])]
        self.assertEqual(create.await_args.kwargs["stream_options"], {"include_usage": True})
        self.assertEqual(responses[-1].content, "ok")
        self.assertIs(responses[-1].raw_response, final)
        self.assertEqual(responses[-1].usage.total_tokens, 6)
        stream.close.assert_awaited_once()

    async def test_vertex_uses_last_usage_snapshot_even_without_candidates(self) -> None:
        async def lines():
            yield 'data: {"usageMetadata":{"totalTokenCount":3},"candidates":[{"content":{"parts":[{"text":"ok"}]}}]}'
            yield 'data: {"usageMetadata":{"totalTokenCount":7},"candidates":[]}'

        @asynccontextmanager
        async def response(*args, **kwargs):
            yield SimpleNamespace(raise_for_status=lambda: None, aiter_lines=lines)

        provider = object.__new__(VertexAIProvider)
        provider.provider_id = "test"
        provider.model_name = "model"
        provider.base_url = "https://example.invalid"
        provider.api_key = "test"
        provider.timeout = 1
        client = SimpleNamespace(stream=response)
        with patch(
            "src.plugins.nonebot_plugin_fxbot.chat.providers.sources.vertex.get_shared_async_client",
            AsyncMock(return_value=client),
        ):
            responses = [response async for response in provider.chat_stream([])]
        self.assertEqual(responses[-1].content, "ok")
        self.assertEqual(responses[-1].usage.to_dict(), {"total_tokens": 7})


@asynccontextmanager
async def _no_stats(*args):
    """隔离数据库统计，专注返回契约。"""
    yield
