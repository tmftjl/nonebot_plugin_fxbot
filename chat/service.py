"""ChatService 编排：历史、Provider 调用、工具循环和持久化。"""

from __future__ import annotations

import json
from typing import Any

from nonebot import logger

from .tools import ToolContext, ToolRuntime, execute_tool, default_registry
from .types import ChatRequest, ChatResponse
from ..config import get_manager as get_config_manager
from .session import ChatSessionStore, default_session_store
from .personas import get_persona_text, get_bound_persona
from .providers import LLMResponse, provider_manager


def _finalize_response(response: LLMResponse) -> LLMResponse:
    """丢弃工具轮数用尽后残留的工具调用，避免写坏历史。"""
    if not response.has_tool_calls:
        return response
    response.tool_calls = []
    response.tool_call_ids = []
    response.content = response.content or "这次操作步骤太多，请换个说法再试。"
    return response


def _build_user_content(request: ChatRequest) -> str | list[dict[str, Any]]:
    """构造用户消息内容，图片按 OpenAI 多模态格式附带。"""
    if not request.images:
        return request.text
    content: list[dict[str, Any]] = []
    if request.text:
        content.append({"type": "text", "text": request.text})
    content.extend({"type": "image_url", "image_url": {"url": url}} for url in request.images)
    return content


def _strip_images(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """历史只保留文本，避免每轮重复上传历史图片。"""
    cleaned: list[dict[str, Any]] = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, list):
            texts = [
                str(part.get("text") or "") for part in content if isinstance(part, dict) and part.get("type") == "text"
            ]
            message = {**message, "content": "".join(texts) or "[图片]"}
        cleaned.append(message)
    return cleaned


class ChatService:
    """AI 对话编排服务。"""

    def __init__(self, session_store: ChatSessionStore = default_session_store) -> None:
        self.session_store = session_store

    async def process(self, request: ChatRequest, runtime: ToolRuntime | None = None) -> ChatResponse:
        """处理一次对话请求。"""
        async with self.session_store.lock(request.session_id):
            return await self._process(request, runtime)

    async def _process(self, request: ChatRequest, runtime: ToolRuntime | None) -> ChatResponse:
        """在会话锁内完成一轮对话。"""
        chat_cfg = get_config_manager().get_system()["chat"]
        self.session_store.max_messages = int(chat_cfg["max_history"])
        provider = await provider_manager.get_provider()
        runtime = runtime or ToolRuntime()
        messages = self.session_store.get(request.session_id)
        persona_name = request.metadata.get("persona_name") if isinstance(request.metadata, dict) else None
        bound_persona = get_bound_persona(request.session_id)
        persona_text = get_persona_text(str(persona_name or bound_persona or chat_cfg["persona"]))
        if persona_text:
            if not messages or messages[0].get("role") != "system":
                messages.insert(0, {"role": "system", "content": persona_text})
            else:
                messages[0] = {"role": "system", "content": persona_text}
        messages.append({"role": "user", "content": _build_user_content(request)})

        tools = default_registry.to_openai_tools()
        response = await provider.chat(messages=messages, tools=tools or None)
        max_tool_rounds = int(chat_cfg["max_tool_rounds"])

        for _ in range(max_tool_rounds):
            if not response.has_tool_calls:
                break
            messages.append(response.to_message_dict())
            context = ToolContext(
                user_id=request.user_id,
                group_id=request.group_id,
                session_id=request.session_id,
                platform=request.platform,
                metadata=dict(request.metadata),
            )
            for tool_call in response.tool_calls:
                func = tool_call.get("function", {})
                tool_result = await execute_tool(
                    str(func.get("name") or ""),
                    func.get("arguments") or "{}",
                    context,
                    runtime,
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.get("id"),
                        "name": func.get("name"),
                        "content": json.dumps(tool_result, ensure_ascii=False),
                    }
                )
            response = await provider.chat(messages=messages, tools=tools or None)

        if response.has_tool_calls:
            logger.warning(f"[Chat] 达到最大工具轮数，已丢弃残留工具调用: session={request.session_id}")
        response = _finalize_response(response)
        messages.append(response.to_message_dict())
        self.session_store.replace(request.session_id, _strip_images(messages))
        return ChatResponse(text=response.content, raw=response)


chat_service = ChatService()
