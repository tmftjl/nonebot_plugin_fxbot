"""Anthropic Claude Provider。"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any

from anthropic import AsyncAnthropic
from nonebot import logger

from ..base import ChatProvider
from ..entities import LLMResponse
from ..register import register_provider


@register_provider("anthropic_chat", "Anthropic Claude API")
class AnthropicProvider(ChatProvider):
    """Anthropic Claude 对话 Provider。"""

    def __init__(self, provider_id: str, config: dict[str, Any]) -> None:
        super().__init__(provider_id, config)
        self.client = AsyncAnthropic(
            api_key=config["api_key"],
            base_url=config["base_url"],
            timeout=float(config["timeout"]),
        )
        self.model_name = str(config["model"])

    def _convert_to_anthropic_format(
        self,
        messages: list[dict[str, Any]],
    ) -> tuple[str, list[dict[str, Any]]]:
        """将 OpenAI 风格消息转换为 Anthropic 格式。"""
        system_prompt = ""
        converted: list[dict[str, Any]] = []

        for msg in messages:
            role = msg.get("role")
            content = msg.get("content", "")

            if role == "system":
                system_prompt = str(content)
            elif role == "user":
                if isinstance(content, list):
                    blocks: list[dict[str, Any]] = []
                    for item in content:
                        if item.get("type") == "text":
                            blocks.append({"type": "text", "text": item.get("text", "")})
                        elif item.get("type") == "image_url":
                            url = item["image_url"]["url"]
                            if url.startswith("data:"):
                                parts = url.split(";base64,")
                                if len(parts) == 2:
                                    blocks.append(
                                        {
                                            "type": "image",
                                            "source": {
                                                "type": "base64",
                                                "media_type": parts[0].replace("data:", ""),
                                                "data": parts[1],
                                            },
                                        }
                                    )
                    converted.append({"role": "user", "content": blocks})
                else:
                    converted.append({"role": "user", "content": content})
            elif role == "assistant":
                blocks: list[dict[str, Any]] = []
                if isinstance(content, str) and content:
                    blocks.append({"type": "text", "text": content})
                for tool_call in msg.get("tool_calls") or []:
                    args = tool_call["function"]["arguments"]
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except json.JSONDecodeError:
                            logger.warning(f"[AnthropicProvider] 工具参数 JSON 解析失败: {args}")
                            args = {}
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": tool_call["id"],
                            "name": tool_call["function"]["name"],
                            "input": args,
                        }
                    )
                if blocks:
                    converted.append({"role": "assistant", "content": blocks})
            elif role == "tool":
                converted.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": msg.get("tool_call_id"),
                                "content": content,
                            }
                        ],
                    }
                )

        return system_prompt, converted

    async def chat(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        temperature: float = 0.7,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """执行一次 Claude 对话请求。"""
        system_prompt, converted_messages = self._convert_to_anthropic_format(messages)
        params: dict[str, Any] = {
            "model": model or self.model_name,
            "messages": converted_messages,
            "max_tokens": max_tokens or 4096,
            "temperature": temperature,
        }
        if system_prompt:
            params["system"] = system_prompt
        if tools:
            params["tools"] = [
                {
                    "name": tool["function"]["name"],
                    "description": tool["function"].get("description", ""),
                    "input_schema": tool["function"].get("parameters", {}),
                }
                for tool in tools
            ]

        try:
            resp = await self.client.messages.create(**params)
        except Exception as exc:
            logger.error(f"[{self.provider_id}] Claude 请求失败: {exc}")
            raise

        response = LLMResponse(role="assistant", raw_response=resp)
        for block in resp.content:
            if block.type == "text":
                response.content = block.text
            elif block.type == "tool_use":
                response.tool_calls.append(
                    {
                        "id": block.id,
                        "type": "function",
                        "function": {
                            "name": block.name,
                            "arguments": json.dumps(block.input)
                            if isinstance(block.input, dict)
                            else str(block.input),
                        },
                    }
                )
                response.tool_call_ids.append(block.id)
        return response

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        temperature: float = 0.7,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncGenerator[LLMResponse, None]:
        """执行 Claude 流式对话请求。"""
        system_prompt, converted_messages = self._convert_to_anthropic_format(messages)
        params: dict[str, Any] = {
            "model": model or self.model_name,
            "messages": converted_messages,
            "max_tokens": 4096,
            "temperature": temperature,
        }
        if system_prompt:
            params["system"] = system_prompt

        try:
            full_content = ""
            async with self.client.messages.stream(**params) as stream:
                async for event in stream:
                    if getattr(event, "type", None) == "content_block_delta" and hasattr(
                        event.delta,
                        "text",
                    ):
                        full_content += event.delta.text
                        yield LLMResponse(
                            role="assistant",
                            content=event.delta.text,
                            is_stream_chunk=True,
                        )
            yield LLMResponse(role="assistant", content=full_content)
        except Exception as exc:
            logger.error(f"[{self.provider_id}] Claude stream 失败: {exc}")
            raise

    async def chat_json(
        self,
        messages: list[dict[str, Any]],
        schema: dict[str, Any] | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        """通过提示词约束获取 JSON 输出。"""
        enhanced_messages = [dict(message) for message in messages]
        instruction = "\n\n请以 JSON 格式输出结果。只输出 JSON，不要包含任何其他文字。"
        if schema:
            instruction += (
                "\n\n请严格按照以下 JSON Schema 输出：\n```json\n"
                f"{json.dumps(schema, indent=2, ensure_ascii=False)}\n```"
            )

        for index in range(len(enhanced_messages) - 1, -1, -1):
            if enhanced_messages[index].get("role") == "user":
                content = enhanced_messages[index].get("content", "")
                if isinstance(content, str):
                    enhanced_messages[index]["content"] = content + instruction
                break

        try:
            response = await self.chat(enhanced_messages, model=model, temperature=0.1)
            content = response.content.strip()
            if content.startswith("```"):
                lines = content.split("\n")
                content = "\n".join(lines[1:-1]) if len(lines) > 2 else content
                content = content.replace("```json", "").replace("```", "").strip()
            return json.loads(content)
        except json.JSONDecodeError as exc:
            logger.warning(f"[{self.provider_id}] JSON 解析失败: {exc}")
            return {}
        except Exception as exc:
            logger.error(f"[{self.provider_id}] chat_json 失败: {exc}")
            return {}
