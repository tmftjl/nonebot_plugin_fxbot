"""Google Vertex AI REST Provider。"""

from __future__ import annotations

import json
from typing import Any
from collections.abc import AsyncGenerator

import httpx
from nonebot import logger

from ..base import ChatProvider
from ..entities import LLMResponse, JSONResponse, LLMResponseError
from ..register import register_provider
from ....utils.http import get_shared_async_client


@register_provider(
    "vertex_chat",
    "Google Vertex AI REST API",
    default_config={"base_url": "", "timeout": 60},
)
class VertexAIProvider(ChatProvider):
    """使用 REST API 调用 Vertex AI 的 Gemini 模型。"""

    def __init__(self, provider_id: str, config: dict[str, Any]) -> None:
        super().__init__(provider_id, config)
        self.api_key = config["api_key"]
        if not self.api_key:
            raise ValueError("Vertex AI provider requires 'api_key'")

        self.base_url = config["base_url"]
        self.timeout = float(config["timeout"])
        self.model_name = str(config["model"])

    def _convert_to_vertex_format(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        """将 OpenAI 风格消息转换为 Vertex AI 格式；多条系统消息按顺序合并。"""
        system_chunks: list[str] = []
        contents: list[dict[str, Any]] = []

        for msg in messages:
            role = msg.get("role")
            content = msg.get("content", "")

            if role == "system":
                system_chunks.append(str(content))
            elif role == "user":
                if isinstance(content, list):
                    parts = []
                    for item in content:
                        if item.get("type") == "text":
                            parts.append({"text": item.get("text", "")})
                        elif item.get("type") == "image_url":
                            url = item["image_url"]["url"]
                            if url.startswith("data:"):
                                mime, data = url.split(";base64,")
                                parts.append(
                                    {
                                        "inlineData": {
                                            "mimeType": mime.replace("data:", ""),
                                            "data": data,
                                        }
                                    }
                                )
                    contents.append({"role": "user", "parts": parts})
                else:
                    contents.append({"role": "user", "parts": [{"text": content or " "}]})
            elif role == "assistant":
                vertex_parts = (msg.get("provider_data") or {}).get("vertex_parts")
                if vertex_parts:
                    contents.append({"role": "model", "parts": vertex_parts})
                    continue

                parts = []
                if content:
                    parts.append({"text": content})
                for tool_call in msg.get("tool_calls") or []:
                    func = tool_call.get("function", {})
                    try:
                        args = json.loads(func.get("arguments", "{}"))
                    except Exception:
                        args = {}
                    parts.append({"functionCall": {"name": func.get("name"), "args": args}})
                if parts:
                    contents.append({"role": "model", "parts": parts})
            elif role == "tool":
                contents.append(
                    {
                        "role": "user",
                        "parts": [
                            {
                                "functionResponse": {
                                    "name": msg.get("name"),
                                    "response": {"result": msg.get("content")},
                                }
                            }
                        ],
                    }
                )

        result: dict[str, Any] = {"contents": contents}
        system = "\n\n".join(chunk for chunk in system_chunks if chunk.strip())
        if system:
            result["systemInstruction"] = {"parts": [{"text": system}]}
        return result

    def _convert_tools_to_vertex_format(
        self,
        tools: list[dict[str, Any]] | None,
    ) -> dict[str, Any] | None:
        """将 OpenAI 风格工具转换为 Vertex AI 格式。"""
        if not tools:
            return None
        return {
            "functionDeclarations": [
                {
                    "name": tool.get("function", {}).get("name", ""),
                    "description": tool.get("function", {}).get("description", ""),
                    "parameters": tool.get("function", {}).get("parameters", {}),
                }
                for tool in tools
            ]
        }

    def _build_endpoint(self, model: str, *, stream: bool = False) -> str:
        """构建 Vertex AI 请求地址。"""
        action = "streamGenerateContent" if stream else "generateContent"
        return f"{self.base_url}/publishers/google/models/{model}:{action}"

    async def chat(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        temperature: float = 0.7,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """执行一次 Vertex AI 对话请求。"""
        model_name = model or self.model_name
        request_body = self._convert_to_vertex_format(messages)
        generation_config: dict[str, Any] = {"temperature": temperature}
        if max_tokens:
            generation_config["maxOutputTokens"] = max_tokens
        request_body["generationConfig"] = generation_config
        if tools_config := self._convert_tools_to_vertex_format(tools):
            request_body["tools"] = [tools_config]

        try:
            client = await get_shared_async_client()
            response = await client.post(
                f"{self._build_endpoint(model_name)}?key={self.api_key}",
                json=request_body,
                headers={"Content-Type": "application/json"},
                timeout=self.timeout,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.error(f"[{self.provider_id}] HTTP 错误: {exc.response.status_code} | response={exc.response.text}")
            raise
        except Exception as exc:
            logger.error(f"[{self.provider_id}] Chat 请求失败: {exc}")
            raise

        data = response.json()
        if not data.get("candidates"):
            raise LLMResponseError(LLMResponse(raw_response=data), "Vertex AI 返回结果为空")

        content_parts = data["candidates"][0].get("content", {}).get("parts", [])
        result = LLMResponse(role="assistant", raw_response=data)
        for part in content_parts:
            if "text" in part:
                result.content += part["text"]
            elif "functionCall" in part:
                func_call = part["functionCall"]
                result.tool_calls.append(
                    {
                        "id": func_call.get("name"),
                        "type": "function",
                        "function": {
                            "name": func_call.get("name"),
                            "arguments": json.dumps(func_call.get("args", {})),
                        },
                    }
                )
        if content_parts:
            result.provider_data = {"vertex_parts": content_parts}
        return result

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        temperature: float = 0.7,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
    ) -> AsyncGenerator[LLMResponse, None]:
        """执行 Vertex AI 流式对话请求。"""
        model_name = model or self.model_name
        request_body = self._convert_to_vertex_format(messages)
        request_body["generationConfig"] = {"temperature": temperature}
        if max_tokens is not None:
            request_body["generationConfig"]["maxOutputTokens"] = max_tokens
        if tools_config := self._convert_tools_to_vertex_format(tools):
            request_body["tools"] = [tools_config]

        try:
            client = await get_shared_async_client()
            async with client.stream(
                "POST",
                f"{self._build_endpoint(model_name, stream=True)}?alt=sse&key={self.api_key}",
                json=request_body,
                headers={"Content-Type": "application/json"},
                timeout=self.timeout,
            ) as response:
                response.raise_for_status()
                full_content = ""
                calls: list[dict[str, Any]] = []
                original_parts: list[dict[str, Any]] = []
                usage_chunk = None
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    try:
                        data = json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        logger.warning(f"[{self.provider_id}] 无法解析流式响应: {line}")
                        continue
                    if data.get("usageMetadata") is not None:
                        usage_chunk = data
                    candidates = data.get("candidates") or [{}]
                    for part in candidates[0].get("content", {}).get("parts", []):
                        original_parts.append(part)
                        if "text" in part:
                            full_content += part["text"]
                            yield LLMResponse(
                                role="assistant",
                                content=part["text"],
                                is_stream_chunk=True,
                            )
                        elif "functionCall" in part:
                            function = part["functionCall"]
                            calls.append(
                                {
                                    "id": function.get("id") or f"call-{len(calls)}",
                                    "type": "function",
                                    "function": {
                                        "name": function.get("name"),
                                        "arguments": json.dumps(function.get("args", {})),
                                    },
                                }
                            )
                yield LLMResponse(
                    role="assistant",
                    content=full_content,
                    tool_calls=calls,
                    provider_data={"vertex_parts": original_parts},
                    raw_response=usage_chunk,
                )
        except httpx.HTTPStatusError as exc:
            logger.error(f"[{self.provider_id}] Stream HTTP 错误: {exc.response.status_code}")
            raise
        except Exception as exc:
            logger.error(f"[{self.provider_id}] Stream 请求失败: {exc}")
            raise

    async def chat_json(
        self,
        messages: list[dict[str, Any]],
        schema: dict[str, Any] | None = None,
        model: str | None = None,
    ) -> JSONResponse:
        """请求 Vertex AI JSON 结构化输出。"""
        model_name = model or self.model_name
        request_body = self._convert_to_vertex_format(messages)
        generation_config: dict[str, Any] = {
            "temperature": 0.1,
            "responseMimeType": "application/json",
        }
        if schema:
            generation_config["responseSchema"] = schema
        request_body["generationConfig"] = generation_config

        try:
            client = await get_shared_async_client()
            response = await client.post(
                f"{self._build_endpoint(model_name)}?key={self.api_key}",
                json=request_body,
                headers={"Content-Type": "application/json"},
                timeout=self.timeout,
            )
            response.raise_for_status()
        except Exception as exc:
            logger.error(f"[{self.provider_id}] chat_json 失败: {exc}")
            raise
        data = response.json()
        candidates = data.get("candidates") or []
        parts = candidates[0].get("content", {}).get("parts", []) if candidates else []
        text = "".join(part.get("text", "") for part in parts if not part.get("thought"))
        return JSONResponse.from_response(LLMResponse(content=text, raw_response=data))
