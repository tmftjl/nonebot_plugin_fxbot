"""ChatService 编排：历史、Provider 调用、工具循环和持久化。"""

from __future__ import annotations

import json
from typing import Any

from nonebot import logger

from ..config import get_manager as get_config_manager

from .personas import get_persona_text
from .providers import provider_manager
from .session import ChatSessionStore, default_session_store
from .tools import ToolContext, ToolRuntime, default_registry, execute_tool
from .types import ChatRequest, ChatResponse


class ChatService:
    """AI 对话编排服务。"""

    def __init__(self, session_store: ChatSessionStore = default_session_store) -> None:
        self.session_store = session_store

    async def process(self, request: ChatRequest, runtime: ToolRuntime | None = None) -> ChatResponse:
        """处理一次对话请求。"""
        chat_cfg = get_config_manager().get_system()["chat"]
        self.session_store.max_messages = int(chat_cfg["max_history"])
        provider = provider_manager.get_provider()
        runtime = runtime or ToolRuntime()
        messages = self.session_store.get(request.session_id)
        persona_text = get_persona_text(request.metadata.get("persona_name") if isinstance(request.metadata, dict) else None)
        if persona_text:
            if not messages or messages[0].get("role") != "system":
                messages.insert(0, {"role": "system", "content": persona_text})
            else:
                messages[0] = {"role": "system", "content": persona_text}
        messages.append({"role": "user", "content": request.text})

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

        messages.append(response.to_message_dict())
        self.session_store.replace(request.session_id, messages)
        return ChatResponse(text=response.content, raw=response)


chat_service = ChatService()
