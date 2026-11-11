"""OpenAI Responses API Provider，支持推理条目回放续写。"""

from __future__ import annotations

import json
from typing import Any

from openai import AsyncOpenAI
from nonebot import logger

from ..base import ChatProvider, local_http_client
from ..entities import LLMResponse
from ..register import register_provider

CLIENT_TYPE = "openai_responses"
_FAILED_STATUS = {"failed", "incomplete", "cancelled"}


def _value(item: Any, name: str, default: Any = None) -> Any:
    """兼容 SDK 对象与已序列化字典。"""
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def _serialize_item(item: Any) -> dict[str, Any]:
    """把 Responses 输出条目转成可回放、可落盘的字典。"""
    if isinstance(item, dict):
        return item
    dump = getattr(item, "model_dump", None)
    return dump(exclude_none=True) if callable(dump) else {}


def _content_parts(content: Any) -> list[dict[str, Any]]:
    """把 OpenAI 聊天消息内容转换成 Responses 输入内容块。"""
    if isinstance(content, str):
        return [{"type": "input_text", "text": content}] if content else []
    parts: list[dict[str, Any]] = []
    for item in content or []:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type") or "")
        if kind in {"text", "input_text"}:
            parts.append({"type": "input_text", "text": str(item.get("text") or "")})
        elif kind in {"image_url", "input_image"}:
            image = item.get("image_url")
            url = image.get("url") if isinstance(image, dict) else image
            if url:
                parts.append({"type": "input_image", "image_url": str(url), "detail": "auto"})
    return parts


def _input_items(messages: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """把 FxBot 维护的聊天消息转换成 Responses input items。"""
    items: list[dict[str, Any]] = []
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "")
        content = message.get("content") or ""
        if role in {"system", "developer"}:
            items.append({"role": role, "content": str(content)})
            continue
        if role == "user":
            items.append({"role": "user", "content": _content_parts(content)})
            continue
        if role == "assistant":
            data = message.get("provider_data")
            if isinstance(data, dict) and data.get("client_type") == CLIENT_TYPE and data.get("output_items"):
                # 原生输出携带推理状态和阶段信息，不能用文字与工具调用投影重建。
                items.extend(data["output_items"])
                continue
            for call in message.get("tool_calls") or []:
                function = call.get("function") if isinstance(call, dict) else None
                if not isinstance(function, dict):
                    continue
                items.append(
                    {
                        "type": "function_call",
                        "call_id": str(call.get("id") or ""),
                        "name": str(function.get("name") or ""),
                        "arguments": str(function.get("arguments") or ""),
                    }
                )
            if isinstance(content, str) and content:
                items.append({"role": "assistant", "content": content})
            continue
        if role == "tool":
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": str(message.get("tool_call_id") or ""),
                    "output": str(content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)),
                }
            )
    return items


def _tool_definitions(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """把聊天补全格式的工具定义压平成 Responses 原生格式。"""
    definitions: list[dict[str, Any]] = []
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        function = tool.get("function")
        if isinstance(function, dict):
            definitions.append(
                {
                    "type": "function",
                    "name": str(function.get("name") or ""),
                    "description": str(function.get("description") or ""),
                    "parameters": function.get("parameters") or {"type": "object", "properties": {}},
                }
            )
        elif tool.get("type") == "function" and tool.get("name"):
            definitions.append(tool)
    return definitions


def _reasoning_text(item: Any) -> tuple[list[str], list[str]]:
    """提取 reasoning item 的摘要与明文推理。"""
    summary: list[str] = []
    for part in _value(item, "summary", []) or []:
        text = str(_value(part, "text", "") or "").strip()
        if text:
            summary.append(text)
    reasoning: list[str] = []
    for part in _value(item, "content", []) or []:
        if str(_value(part, "type", "") or "") != "reasoning_text":
            continue
        text = str(_value(part, "text", "") or "").strip()
        if text:
            reasoning.append(text)
    return summary, reasoning


def _parse_response(response: Any, model: str) -> LLMResponse:
    """把 Responses 响应投影成 FxBot 的 LLMResponse。"""
    status = str(_value(response, "status", "") or "")
    if status in _FAILED_STATUS:
        details = _value(response, "error") or _value(response, "incomplete_details") or status
        raise RuntimeError(f"Responses 请求未完整完成: {details}")

    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    output_items: list[dict[str, Any]] = []
    for item in _value(response, "output", []) or []:
        item_type = str(_value(item, "type", "") or "")
        output_items.append(_serialize_item(item))
        if item_type == "message":
            for part in _value(item, "content", []) or []:
                part_type = str(_value(part, "type", "") or "")
                if part_type == "output_text":
                    text = str(_value(part, "text", "") or "")
                    if text:
                        content_parts.append(text)
                elif part_type == "refusal":
                    refusal = str(_value(part, "refusal", "") or "")
                    if refusal:
                        content_parts.append(refusal)
            continue
        if item_type == "reasoning":
            summary, reasoning = _reasoning_text(item)
            reasoning_parts.extend(summary or reasoning)
            continue
        if item_type == "function_call":
            call_id = str(_value(item, "call_id", "") or "").strip()
            name = str(_value(item, "name", "") or "").strip()
            if not call_id or not name:
                raise RuntimeError("Responses function_call 缺少 call_id 或 name")
            tool_calls.append(
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": str(_value(item, "arguments", "") or "")},
                }
            )
    return LLMResponse(
        role="assistant",
        content="".join(content_parts),
        reasoning_content="".join(reasoning_parts),
        tool_calls=tool_calls,
        tool_call_ids=[call["id"] for call in tool_calls],
        provider_data={"client_type": CLIENT_TYPE, "output_items": output_items},
        raw_response=response,
    )


@register_provider(
    f"{CLIENT_TYPE}_chat",
    "OpenAI Responses API",
    default_config={"base_url": "https://api.openai.com/v1", "timeout": 60},
)
class OpenAIResponsesProvider(ChatProvider):
    """使用 Responses API 的对话 Provider；回放 reasoning item 支持续写。"""

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

    async def _create(
        self,
        *,
        model: str,
        input_items: list[dict[str, Any]],
        temperature: float,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """发起一次 Responses 请求并解析。"""
        params: dict[str, Any] = {
            "model": model,
            "input": input_items,
            "temperature": temperature,
            "store": False,
        }
        definitions = _tool_definitions(tools)
        if definitions:
            params["tools"] = definitions
        if max_tokens is not None:
            params["max_output_tokens"] = max_tokens
        try:
            response = await self.client.responses.create(**params)
        except Exception as exc:
            logger.error(f"[{self.provider_id}] Responses 请求失败: {exc}")
            raise
        parsed = _parse_response(response, model)
        if parsed.tool_calls and not parsed.content:
            logger.debug(f"[{self.provider_id}] Responses 返回 {len(parsed.tool_calls)} 个工具调用")
        return parsed

    async def chat(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        temperature: float = 0.7,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """执行一次 Responses 对话请求。"""
        return await self._create(
            model=model or self.model_name,
            input_items=_input_items(messages),
            temperature=temperature,
            tools=tools,
            max_tokens=max_tokens,
        )

    async def chat_json(
        self,
        messages: list[dict[str, Any]],
        schema: dict[str, Any] | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        """使用 Responses 原生 text.format 请求结构化输出。"""
        text_format: dict[str, Any] = {"type": "json_object"}
        if schema:
            text_format = {"type": "json_schema", "name": "response", "schema": schema, "strict": True}
        try:
            response = await self.client.responses.create(
                model=model or self.model_name,
                input=_input_items(messages),
                temperature=0.1,
                store=False,
                text={"format": text_format},
            )
            parsed = _parse_response(response, model or self.model_name)
            return json.loads(parsed.content or "{}")
        except json.JSONDecodeError as exc:
            logger.warning(f"[{self.provider_id}] JSON 解析失败: {exc}")
            raise ValueError(f"[{self.provider_id}] JSON 解析失败: {exc}") from exc
        except Exception as exc:
            logger.error(f"[{self.provider_id}] chat_json 失败: {exc}")
            raise

    async def chat_continuation(
        self,
        messages: list[dict[str, Any]],
        previous: LLMResponse,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> LLMResponse | None:
        """把上一轮原始输出条目接回 input，让模型接着说完正文。"""
        data = previous.provider_data if isinstance(previous.provider_data, dict) else {}
        if data.get("client_type") != CLIENT_TYPE:
            return None
        output_items = data.get("output_items") or []
        if not output_items or not any(item.get("type") == "reasoning" for item in output_items):
            return None
        return await self._create(
            model=model or self.model_name,
            input_items=[*_input_items(messages), *output_items],
            temperature=temperature,
            max_tokens=max_tokens,
        )
