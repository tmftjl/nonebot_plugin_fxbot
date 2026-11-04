"""对齐 MaiBot 回复器的纯文本生成与发送前后处理。"""

from __future__ import annotations

import asyncio
from typing import Any

from nonebot import logger

from ..web import web_runtime
from ..tools import (
    SUBAGENT_TOOL_NAMES,
    SUBAGENT_STOP_TOOL_NAME,
    SUBAGENT_STATUS_TOOL_NAME,
    ToolRuntime,
    discovered,
    tool_allowed,
    tool_visible,
    default_registry,
)
from ..types import ChatRequest
from ..providers import LLMResponse
from ..capabilities import capabilities_config
from ..amemorix.runtime import amemorix_runtime
from ..behavior.response import ProcessedResponseSegment, process_response_async
from ..behavior.postprocess import postprocess_reply

TOOL_TIMEOUT = 15.0
WEB_TOOLS = frozenset({"web_search", "web_fetch"})
PROACTIVE_TOOLS = frozenset(
    {
        "query_memory",
        "query_person_profile",
        "web_search",
        "web_fetch",
        "read_tool_result",
        *SUBAGENT_TOOL_NAMES,
    }
)


def visible_tools(allowed_names: set[str], session_id: str) -> list[dict[str, Any]]:
    """生成工具定义；延迟工具只有被 tool_search 发现后才发给模型。"""
    found = discovered(session_id)
    definitions = []
    for item in default_registry.to_openai_tools():
        name = item["function"]["name"]
        if name not in allowed_names:
            continue
        spec = default_registry.get(name)
        if spec is not None and spec.deferred and name not in found:
            continue
        definitions.append(item)
    return definitions


async def allowed_tools(
    runtime: ToolRuntime, *, proactive: bool = False, session_id: str = ""
) -> tuple[set[str], list[dict[str, Any]]]:
    """按调用者权限过滤工具；主动轮只保留只读工具。"""
    specs = default_registry.list()
    if not amemorix_runtime.available or not amemorix_runtime.config().image_memory.enabled:
        specs = [spec for spec in specs if spec.name != "query_image_memory"]
    memory_tools = {
        "query_memory": "enable_memory_query_tool",
        "query_person_profile": "enable_person_profile_query_tool",
    }
    for spec in specs[:]:
        flag = memory_tools.get(spec.name)
        if flag is None:
            continue
        enabled_by_config = bool(getattr(amemorix_runtime.config().integration, flag, False))
        if not amemorix_runtime.available or not enabled_by_config:
            specs = [item for item in specs if item.name != spec.name]
    if not web_runtime().enabled:
        specs = [spec for spec in specs if spec.name not in WEB_TOOLS]
    capabilities = capabilities_config()
    if not capabilities.get("planner_subagent"):
        specs = [spec for spec in specs if spec.name not in SUBAGENT_TOOL_NAMES]
    elif not capabilities.get("advanced_sub_agent"):
        specs = [s for s in specs if s.name not in {SUBAGENT_STATUS_TOOL_NAME, SUBAGENT_STOP_TOOL_NAME}]
    if proactive:
        specs = [spec for spec in specs if spec.name in PROACTIVE_TOOLS]
    allowed_specs = [spec for spec in specs if await tool_allowed(spec, runtime, session_id=session_id)]
    names = {spec.name for spec in allowed_specs}
    visible = {spec.name for spec in allowed_specs if tool_visible(spec, session_id)}
    tools = visible_tools(visible, session_id)
    return names, tools


async def generate(
    service: Any,
    request: ChatRequest,
    runtime: ToolRuntime,
    provider: Any,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    allowed_names: set[str],
    epoch: int,
    limit: int,
) -> LLMResponse:
    """回复器只生成正文，工具调用由 Planner 执行。"""
    response = await asyncio.wait_for(
        provider.chat(messages=messages, tools=None, temperature=1.0, max_tokens=8192), 240
    )
    if response.has_tool_calls:
        logger.warning("[Chat] 回复模型返回了工具调用，已丢弃该次输出")
        return LLMResponse(content="")
    return response


async def shape_reply(
    response: LLMResponse,
    *,
    provider: Any,
    persona: str,
    post_enabled: bool,
    splitter: dict[str, Any] | None,
) -> tuple[LLMResponse, list[ProcessedResponseSegment], str]:
    """后处理并按配置断句，返回改写后的响应、待发送分段与原始生成文本。"""
    generated = response.content or ""
    cleaned = postprocess_reply(generated) if post_enabled else generated
    segments = (
        await process_response_async(
            cleaned,
            provider=provider,
            nickname=persona,
            post={"enable_response_post_process": post_enabled},
            splitter=splitter,
        )
        if cleaned.strip()
        else []
    )
    response.content = "".join(segment.text for segment in segments)
    return response, segments, generated
