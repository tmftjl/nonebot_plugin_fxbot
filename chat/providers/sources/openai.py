"""OpenAI 及兼容协议 Provider。"""

from __future__ import annotations

import re
from typing import Any
from collections.abc import AsyncGenerator

from openai import AsyncOpenAI
from nonebot import logger

from ..base import STTProvider, TTSProvider, ChatProvider, EmbeddingProvider, local_http_client
from ..entities import LLMResponse, JSONResponse, ProviderType, LLMResponseError
from ..register import register_provider

EMBED_BATCH_LIMIT = 25
_THINK_PATTERN = re.compile(
    r"<think>(?P<think>.*?)</think>(?P<content>.*)|<think>(?P<think_unclosed>.*)|(?P<content_only>.+)",
    re.DOTALL,
)


def _reasoning_of(message: Any) -> str:
    """读取兼容接口附带的思考内容；SDK 未建模该字段时会落在 model_extra。"""
    extra = getattr(message, "model_extra", None) or {}
    for name in ("reasoning_content", "reasoning"):
        value = getattr(message, name, None)
        if value is None:
            value = extra.get(name)
        if value:
            return str(value)
    return ""


def _inline_reasoning(content: str, reasoning: str) -> tuple[str, str]:
    """兼容把思考写进正文的模型；已有原生思考时不解析内联块。"""
    if reasoning or not content.startswith("<think>"):
        return reasoning, content
    match = _THINK_PATTERN.match(content)
    if not match:
        return reasoning, content
    if match.group("think") is not None:
        return match.group("think").strip(), (match.group("content") or "").strip()
    if match.group("think_unclosed") is not None:
        return match.group("think_unclosed").strip(), ""
    return reasoning, (match.group("content_only") or "").strip()


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
            http_client=local_http_client(config["base_url"], float(config["timeout"])),
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
            "messages": [
                {key: value for key, value in message.items() if not key.startswith("_")} for message in messages
            ],
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

        if not resp.choices:
            raise LLMResponseError(LLMResponse(raw_response=resp), "OpenAI 返回结果为空")
        msg = resp.choices[0].message
        reasoning, content = _inline_reasoning(msg.content or "", _reasoning_of(msg))
        response = LLMResponse(
            role="assistant",
            content=content,
            reasoning_content=reasoning,
            raw_response=resp,
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
        max_tokens: int | None = None,
    ) -> AsyncGenerator[LLMResponse, None]:
        """执行 OpenAI 流式对话请求。"""
        params: dict[str, Any] = {
            "model": model or self.model_name,
            "messages": [
                {key: value for key, value in message.items() if not key.startswith("_")} for message in messages
            ],
            "temperature": temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            params["tools"] = tools

        if max_tokens is not None:
            params["max_tokens"] = max_tokens
        try:
            stream = await self.client.chat.completions.create(**params)
            full_content = ""
            full_reasoning = ""
            calls: dict[int, dict[str, Any]] = {}
            usage_chunk = None
            try:
                async for chunk in stream:
                    if getattr(chunk, "usage", None) is not None:
                        usage_chunk = chunk
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    full_reasoning += _reasoning_of(delta)
                    for fragment in delta.tool_calls or []:
                        call = calls.setdefault(
                            fragment.index,
                            {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
                        )
                        if fragment.id:
                            call["id"] = fragment.id
                        if fragment.function:
                            call["function"]["name"] += fragment.function.name or ""
                            call["function"]["arguments"] += fragment.function.arguments or ""
                    if delta.content:
                        full_content += delta.content
                        yield LLMResponse(role="assistant", content=delta.content, is_stream_chunk=True)
            finally:
                await stream.close()
            tool_calls = [calls[index] for index in sorted(calls)]
            reasoning, content = _inline_reasoning(full_content, full_reasoning)
            yield LLMResponse(
                role="assistant",
                content=content,
                reasoning_content=reasoning,
                tool_calls=tool_calls,
                tool_call_ids=[call["id"] for call in tool_calls],
                raw_response=usage_chunk,
            )
        except Exception as exc:
            logger.error(f"[{self.provider_id}] Stream 请求失败: {exc}")
            raise

    async def chat_json(
        self,
        messages: list[dict[str, Any]],
        schema: dict[str, Any] | None = None,
        model: str | None = None,
    ) -> JSONResponse:
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
        except Exception as exc:
            logger.error(f"[{self.provider_id}] chat_json 失败: {exc}")
            raise
        if not resp.choices:
            return JSONResponse.from_response(LLMResponse(raw_response=resp))
        msg = resp.choices[0].message
        reasoning, content = _inline_reasoning(msg.content or "", _reasoning_of(msg))
        return JSONResponse.from_response(LLMResponse(content=content, reasoning_content=reasoning, raw_response=resp))

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
    default_config={
        "base_url": "https://api.openai.com/v1",
        "timeout": 60,
        "embedding_dim": 1536,
        "embed_batch_limit": EMBED_BATCH_LIMIT,
    },
)
class OpenAIEmbeddingProvider(EmbeddingProvider):
    """OpenAI 嵌入 Provider。"""

    def __init__(self, provider_id: str, config: dict[str, Any]) -> None:
        super().__init__(provider_id, config)
        self.client = AsyncOpenAI(
            api_key=config["api_key"],
            base_url=config["base_url"],
            timeout=float(config["timeout"]),
            http_client=local_http_client(config["base_url"], float(config["timeout"])),
        )
        self.model_name = str(config["model"])

    async def close(self) -> None:
        """关闭 OpenAI 异步客户端。"""
        try:
            await self.client.close()
        except Exception:
            logger.opt(exception=True).warning(f"[{self.provider_id}] 关闭客户端失败")

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """批量获取文本嵌入向量；服务端单次条数上限各不相同，按配置切分后合并。"""
        limit = max(1, int(self.config.get("embed_batch_limit") or EMBED_BATCH_LIMIT))
        vectors: list[list[float]] = []
        try:
            for start in range(0, len(texts), limit):
                batch = texts[start : start + limit]
                resp = await self.client.embeddings.create(input=batch, model=self.model_name)
                vectors.extend(data.embedding for data in resp.data)
            return vectors
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
            http_client=local_http_client(config["base_url"], float(config["timeout"])),
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
            http_client=local_http_client(config["base_url"], float(config["timeout"])),
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
