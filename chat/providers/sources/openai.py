"""OpenAI 及兼容协议 Provider。"""

from __future__ import annotations

import json
from typing import Any
from collections.abc import AsyncGenerator

from openai import AsyncOpenAI
from nonebot import logger

from ..base import STTProvider, TTSProvider, ChatProvider, EmbeddingProvider
from ..entities import LLMResponse, ProviderType
from ..register import register_provider


@register_provider(
    "openai_chat",
    "OpenAI / 兼容 API",
    default_config={"base_url": "https://api.openai.com/v1", "timeout": 60},
)
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

    async def close(self) -> None:
        """关闭 OpenAI 异步客户端。"""
        try:
            await self.client.close()
        except Exception:
            logger.opt(exception=True).warning(f"[{self.provider_id}] 关闭客户端失败")

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
        response = LLMResponse(role="assistant", content=msg.content or "", raw_response=resp)
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


@register_provider(
    "openai_embedding",
    "OpenAI Embedding",
    provider_type=ProviderType.EMBEDDING,
    default_config={"base_url": "https://api.openai.com/v1", "timeout": 60, "embedding_dim": 1536},
)
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

    async def close(self) -> None:
        """关闭 OpenAI 异步客户端。"""
        try:
            await self.client.close()
        except Exception:
            logger.opt(exception=True).warning(f"[{self.provider_id}] 关闭客户端失败")

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """批量获取文本嵌入向量。"""
        try:
            resp = await self.client.embeddings.create(input=texts, model=self.model_name)
            return [data.embedding for data in resp.data]
        except Exception as exc:
            logger.error(f"[{self.provider_id}] Embed 请求失败: {exc}")
            raise


@register_provider(
    "openai_stt",
    "OpenAI 语音识别",
    provider_type=ProviderType.STT,
    default_config={"base_url": "https://api.openai.com/v1", "timeout": 60},
)
class OpenAISTTProvider(STTProvider):
    """OpenAI 语音转写 Provider。"""

    def __init__(self, provider_id: str, config: dict[str, Any]) -> None:
        super().__init__(provider_id, config)
        self.client = AsyncOpenAI(
            api_key=config["api_key"],
            base_url=config["base_url"],
            timeout=float(config["timeout"]),
        )
        self.model_name = str(config["model"])

    async def close(self) -> None:
        """关闭 OpenAI 异步客户端。"""
        try:
            await self.client.close()
        except Exception:
            logger.opt(exception=True).warning(f"[{self.provider_id}] 关闭客户端失败")

    async def transcribe(self, audio: bytes, filename: str = "voice.mp3") -> str:
        """把音频转成文字；接口不支持的语言或格式会抛错并由调用方降级。"""
        try:
            resp = await self.client.audio.transcriptions.create(
                model=self.model_name,
                file=(filename, audio),
            )
            return str(resp.text or "").strip()
        except Exception as exc:
            logger.error(f"[{self.provider_id}] 语音转写失败: {exc}")
            raise


@register_provider(
    "openai_tts",
    "OpenAI 语音合成",
    provider_type=ProviderType.TTS,
    default_config={"base_url": "https://api.openai.com/v1", "timeout": 60, "voice": "alloy"},
)
class OpenAITTSProvider(TTSProvider):
    """OpenAI 语音合成 Provider。"""

    def __init__(self, provider_id: str, config: dict[str, Any]) -> None:
        super().__init__(provider_id, config)
        self.client = AsyncOpenAI(
            api_key=config["api_key"],
            base_url=config["base_url"],
            timeout=float(config["timeout"]),
        )
        self.model_name = str(config["model"])

    async def close(self) -> None:
        """关闭 OpenAI 异步客户端。"""
        try:
            await self.client.close()
        except Exception:
            logger.opt(exception=True).warning(f"[{self.provider_id}] 关闭客户端失败")

    async def synthesize(self, text: str, voice: str = "default") -> bytes:
        """把文本合成为 mp3 音频；失败时抛错由调用方降级。"""
        voice_name = str(self.config.get("voice") or "") or ("alloy" if voice == "default" else voice)
        try:
            resp = await self.client.audio.speech.create(
                model=self.model_name,
                voice=voice_name,
                input=text,
                response_format="mp3",
            )
            return resp.content
        except Exception as exc:
            logger.error(f"[{self.provider_id}] 语音合成失败: {exc}")
            raise
