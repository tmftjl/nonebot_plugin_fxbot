"""Google Gemini Provider。"""

from __future__ import annotations

import base64
import json
from collections.abc import AsyncGenerator
from typing import Any

from google import genai
from google.genai import types
from nonebot import logger

from ..base import ChatProvider, EmbeddingProvider
from ..entities import LLMResponse
from ..register import register_provider


@register_provider("gemini_chat", "Google Gemini API")
class GeminiProvider(ChatProvider):
    """Gemini 对话 Provider。"""

    def __init__(self, provider_id: str, config: dict[str, Any]) -> None:
        super().__init__(provider_id, config)
        self.api_key = config["api_key"]
        self.base_url = config["base_url"]
        self.timeout = float(config["timeout"])
        self.model_name = str(config["model"])
        self._init_client()

    def _init_client(self) -> None:
        """初始化 Gemini 异步客户端。"""
        self.client = genai.Client(
            api_key=self.api_key,
            http_options=types.HttpOptions(
                base_url=self.base_url,
                timeout=self.timeout * 1000,
            ),
        ).aio

    def _convert_to_gemini_format(
        self,
        messages: list[dict[str, Any]],
    ) -> tuple[str | None, list[Any]]:
        """将 OpenAI 风格消息转换为 Gemini 格式。"""
        system_instruction = None
        contents: list[Any] = []

        for msg in messages:
            role = msg.get("role")
            content = msg.get("content", "")

            if role == "system":
                system_instruction = str(content)
            elif role == "user":
                if isinstance(content, list):
                    parts = []
                    for item in content:
                        if item.get("type") == "text":
                            parts.append(types.Part.from_text(text=item.get("text", "")))
                        elif item.get("type") == "image_url":
                            url = item["image_url"]["url"]
                            if url.startswith("data:"):
                                mime, data = url.split(";base64,")
                                parts.append(
                                    types.Part.from_bytes(
                                        data=base64.b64decode(data),
                                        mime_type=mime.replace("data:", ""),
                                    )
                                )
                    contents.append(types.UserContent(parts=parts))
                else:
                    contents.append(
                        types.UserContent(parts=[types.Part.from_text(text=content or " ")])
                    )
            elif role == "assistant":
                parts = []
                if content:
                    parts.append(types.Part.from_text(text=content))
                for tool_call in msg.get("tool_calls") or []:
                    func = tool_call.get("function", {})
                    try:
                        args = json.loads(func.get("arguments", "{}"))
                    except Exception:
                        args = {}
                    parts.append(types.Part.from_function_call(name=func.get("name"), args=args))
                if parts:
                    contents.append(types.ModelContent(parts=parts))
            elif role == "tool":
                parts = [
                    types.Part.from_function_response(
                        name=msg.get("name"),
                        response={"result": msg.get("content")},
                    )
                ]
                contents.append(types.UserContent(parts=parts))

        return system_instruction, contents

    def _convert_tools_to_gemini_format(
        self,
        tools: list[dict[str, Any]] | None,
    ) -> list[Any] | None:
        """将 OpenAI 风格工具转换为 Gemini 格式。"""
        if not tools:
            return None

        function_declarations = []
        for tool in tools:
            func = tool.get("function", {})
            function_declarations.append(
                types.FunctionDeclaration(
                    name=func.get("name", ""),
                    description=func.get("description", ""),
                    parameters=func.get("parameters", {}),
                )
            )
        return [types.Tool(function_declarations=function_declarations)]

    async def chat(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        temperature: float = 0.7,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """执行一次 Gemini 对话请求。"""
        system_instruction, contents = self._convert_to_gemini_format(messages)
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=temperature,
            max_output_tokens=max_tokens,
            tools=self._convert_tools_to_gemini_format(tools),
        )

        try:
            resp = await self.client.models.generate_content(
                model=model or self.model_name,
                contents=contents,
                config=config,
            )
        except Exception as exc:
            logger.error(f"[{self.provider_id}] Gemini 请求失败: {exc}")
            raise

        if not resp.candidates:
            raise ValueError("Gemini 返回结果为空")

        candidate = resp.candidates[0]
        response = LLMResponse(role="assistant", raw_response=resp)
        for part in candidate.content.parts:
            if part.text:
                response.content += part.text
            elif part.function_call:
                response.tool_calls.append(
                    {
                        "id": part.function_call.id or part.function_call.name,
                        "type": "function",
                        "function": {
                            "name": part.function_call.name,
                            "arguments": json.dumps(dict(part.function_call.args)),
                        },
                    }
                )
        return response

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        temperature: float = 0.7,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncGenerator[LLMResponse, None]:
        """执行 Gemini 流式对话请求。"""
        system_instruction, contents = self._convert_to_gemini_format(messages)
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=temperature,
        )

        try:
            stream = await self.client.models.generate_content_stream(
                model=model or self.model_name,
                contents=contents,
                config=config,
            )
            full_content = ""
            async for chunk in stream:
                if chunk.text:
                    full_content += chunk.text
                    yield LLMResponse(
                        role="assistant",
                        content=chunk.text,
                        is_stream_chunk=True,
                    )
            yield LLMResponse(role="assistant", content=full_content)
        except Exception as exc:
            logger.error(f"[{self.provider_id}] Gemini stream 失败: {exc}")
            raise

    async def chat_json(
        self,
        messages: list[dict[str, Any]],
        schema: dict[str, Any] | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        """使用 Gemini JSON 模式请求结构化输出。"""
        system_instruction, contents = self._convert_to_gemini_format(messages)
        config: dict[str, Any] = {
            "system_instruction": system_instruction,
            "temperature": 0.1,
            "response_mime_type": "application/json",
        }
        if schema:
            config["response_json_schema"] = schema

        try:
            resp = await self.client.models.generate_content(
                model=model or self.model_name,
                contents=contents,
                config=config,
            )
            if not resp.candidates:
                logger.warning(f"[{self.provider_id}] Gemini 返回结果为空")
                return {}
            content = resp.candidates[0].content.parts[0].text
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
            return [
                model.name.replace("models/", "")
                for model in models
                if model.supported_actions and "generateContent" in model.supported_actions
            ]
        except Exception as exc:
            logger.warning(f"[{self.provider_id}] 获取模型列表失败: {exc}")
            return []


@register_provider("gemini_embedding", "Google Gemini Embedding")
class GeminiEmbeddingProvider(EmbeddingProvider):
    """Gemini 嵌入 Provider。"""

    def __init__(self, provider_id: str, config: dict[str, Any]) -> None:
        super().__init__(provider_id, config)
        http_options = types.HttpOptions(timeout=float(config["timeout"]) * 1000)
        if config["base_url"]:
            http_options.base_url = config["base_url"]
        self.client = genai.Client(
            api_key=config["api_key"],
            http_options=http_options,
        ).aio
        self.model_name = str(config["model"])

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """批量获取文本嵌入向量。"""
        try:
            resp = await self.client.models.embed_content(model=self.model_name, contents=texts)
            return [list(embedding.values) for embedding in resp.embeddings]
        except Exception as exc:
            logger.error(f"[{self.provider_id}] Gemini embed 失败: {exc}")
            raise

    def get_dimension(self) -> int:
        """读取配置中的向量维度。"""
        return int(self.config["embedding_dim"])
