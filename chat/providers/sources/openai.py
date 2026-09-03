"""OpenAI 及兼容协议 Provider。"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any

from nonebot import logger
from openai import AsyncOpenAI

from ..base import ChatProvider, EmbeddingProvider
from ..entities import LLMResponse
from ..register import register_provider


@register_provider("openai_chat", "OpenAI / 兼容 API")
class OpenAIProvider(ChatProvider):
    """OpenAI 协议对话 Provider。"""

    def __init__(self, provider_id: str, config: dict[str, Any]) -> None:
        super().__init__(provider_id, config)
        self.client = AsyncOpenAI(
            api_key=config["api_key"],
            base_url=config["base_url"],
            timeout=float(config["timeout"]),
        )
        self.model_name = str(config["model"])

    async def chat(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        temperature: float = 0.7,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """执行一次 OpenAI 对话请求。"""
        params: dict[str, Any] = {
            "model": model or self.model_name,
            "messages": messages,
            "temperature": temperature,
        }
        if tools:
            params["tools"] = tools
            params["tool_choice"] = "auto"
        if max_tokens:
            params["max_tokens"] = max_tokens

        try:
            resp = await self.client.chat.completions.create(**params)
        except Exception as exc:
            logger.error(f"[{self.provider_id}] Chat 请求失败: {exc}")
            raise

        msg = resp.choices[0].message
        response = LLMResponse(
            role="assistant", content=msg.content or "", raw_response=resp
        )
        if msg.tool_calls:
            response.tool_calls = [
                {
                    "id": tool_call.id,
                    "type": "function",
                    "function": {
                        "name": tool_call.function.name,
                        "arguments": tool_call.function.arguments,
                    },
                }
                for tool_call in msg.tool_calls
            ]
            response.tool_call_ids = [tool_call.id for tool_call in msg.tool_calls]
        return response

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        temperature: float = 0.7,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncGenerator[LLMResponse, None]:
        """执行 OpenAI 流式对话请求。"""
        params: dict[str, Any] = {
            "model": model or self.model_name,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        }
        if tools:
            params["tools"] = tools

        try:
            stream = await self.client.chat.completions.create(**params)
            full_content = ""
            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta.content:
                    full_content += delta.content
                    yield LLMResponse(
                        role="assistant",
                        content=delta.content,
                        is_stream_chunk=True,
                    )
            yield LLMResponse(role="assistant", content=full_content)
        except Exception as exc:
            logger.error(f"[{self.provider_id}] Stream 请求失败: {exc}")
            raise

    async def chat_json(
        self,
        messages: list[dict[str, Any]],
        schema: dict[str, Any] | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        """使用 OpenAI JSON 模式请求结构化输出。"""
        params: dict[str, Any] = {
            "model": model or self.model_name,
            "messages": messages,
            "temperature": 0.1,
        }
        if schema:
            params["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "response",
                    "schema": schema,
                    "strict": True,
                },
            }
        else:
            params["response_format"] = {"type": "json_object"}

        try:
            resp = await self.client.chat.completions.create(**params)
            content = resp.choices[0].message.content or "{}"
            return json.loads(content)
        except json.JSONDecodeError as exc:
            logger.warning(f"[{self.provider_id}] JSON 解析失败: {exc}")
            return {}
        except Exception as exc:
            logger.error(f"[{self.provider_id}] chat_json 失败: {exc}")
            return {}

    async def get_models(self) -> list[str]:
        """获取可用模型列表。"""
        try:
            models = await self.client.models.list()
            return sorted([model.id for model in models.data])
        except Exception as exc:
            logger.warning(f"[{self.provider_id}] 获取模型列表失败: {exc}")
            return []


@register_provider("openai_embedding", "OpenAI Embedding")
class OpenAIEmbeddingProvider(EmbeddingProvider):
    """OpenAI 嵌入 Provider。"""

    def __init__(self, provider_id: str, config: dict[str, Any]) -> None:
        super().__init__(provider_id, config)
        self.client = AsyncOpenAI(
            api_key=config["api_key"],
            base_url=config["base_url"],
            timeout=float(config["timeout"]),
        )
        self.model_name = str(config["model"])

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """批量获取文本嵌入向量。"""
        try:
            resp = await self.client.embeddings.create(
                input=texts, model=self.model_name
            )
            return [data.embedding for data in resp.data]
        except Exception as exc:
            logger.error(f"[{self.provider_id}] Embed 请求失败: {exc}")
            raise
