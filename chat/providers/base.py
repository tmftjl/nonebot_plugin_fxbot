"""Provider 抽象基类，按 temp/core/agent/providers/base.py 适配。"""

from __future__ import annotations

import abc
import asyncio
import json
from collections.abc import AsyncGenerator
from typing import Any

from nonebot import logger

from .entities import LLMResponse, ProviderMeta, ProviderType


class BaseProvider(abc.ABC):
    """Provider 抽象基类。"""

    def __init__(self, provider_id: str, config: dict[str, Any]) -> None:
        self.provider_id = provider_id
        self.config = config
        self.model_name = str(config.get("model") or "")

    def get_model(self) -> str:
        """获取当前模型名。"""
        return self.model_name

    def set_model(self, model: str) -> None:
        """设置当前模型名。"""
        self.model_name = model

    def meta(self) -> ProviderMeta:
        """返回 Provider 元数据。"""
        return ProviderMeta(
            id=self.provider_id,
            model=self.model_name,
            type=self.__class__.__name__,
            provider_type=ProviderType.CHAT,
        )


class ChatProvider(BaseProvider):
    """对话能力 Provider。"""

    @abc.abstractmethod
    async def chat(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        temperature: float = 0.7,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """执行一次对话请求。"""

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        temperature: float = 0.7,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncGenerator[LLMResponse, None]:
        """流式对话，默认降级为普通对话。"""
        response = await self.chat(messages, model, temperature, tools)
        yield response

    async def chat_json(
        self,
        messages: list[dict[str, Any]],
        schema: dict[str, Any] | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        """结构化 JSON 输出，默认尝试解析文本。"""
        response = await self.chat(messages, model, temperature=0.1)
        try:
            return json.loads(response.content)
        except json.JSONDecodeError:
            logger.warning(f"[{self.provider_id}] JSON 解析失败")
            return {}

    async def test(self, timeout: float = 30.0) -> bool:
        """测试 Provider 连接。"""
        try:
            await asyncio.wait_for(
                self.chat([{"role": "user", "content": "ping"}]),
                timeout=timeout,
            )
            return True
        except Exception as exc:
            logger.error(f"[{self.provider_id}] 测试失败: {exc}")
            return False


class EmbeddingProvider(BaseProvider):
    """嵌入能力 Provider。"""

    @abc.abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """批量获取文本嵌入向量。"""

    async def embed_single(self, text: str) -> list[float]:
        """获取单条文本嵌入向量。"""
        result = await self.embed([text])
        return result[0] if result else []

    def get_dimension(self) -> int:
        """获取向量维度。"""
        return int(self.config.get("embedding_dim", 1536))

    def meta(self) -> ProviderMeta:
        """返回嵌入 Provider 元数据。"""
        return ProviderMeta(
            id=self.provider_id,
            model=self.model_name,
            type=self.__class__.__name__,
            provider_type=ProviderType.EMBEDDING,
        )


class TTSProvider(BaseProvider):
    """语音合成能力 Provider。"""

    @abc.abstractmethod
    async def synthesize(self, text: str, voice: str = "default") -> str:
        """合成语音，返回音频文件路径。"""

    def meta(self) -> ProviderMeta:
        """返回语音合成 Provider 元数据。"""
        return ProviderMeta(
            id=self.provider_id,
            model=self.model_name,
            type=self.__class__.__name__,
            provider_type=ProviderType.TTS,
        )


class STTProvider(BaseProvider):
    """语音识别能力 Provider。"""

    @abc.abstractmethod
    async def transcribe(self, audio_path: str) -> str:
        """识别音频并返回文本。"""

    def meta(self) -> ProviderMeta:
        """返回语音识别 Provider 元数据。"""
        return ProviderMeta(
            id=self.provider_id,
            model=self.model_name,
            type=self.__class__.__name__,
            provider_type=ProviderType.STT,
        )
