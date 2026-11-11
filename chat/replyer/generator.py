"""对齐 MaiBot 回复器的纯文本生成与发送前后处理。"""

from __future__ import annotations

import asyncio
from typing import Any

from nonebot import logger

from ..web import web_runtime
from ..hooks import ChatHookContext, hook_context
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
from ..stages import execute_stage
from ..providers import LLMResponse
from ..capabilities import capabilities_config
from ..amemorix.runtime import amemorix_runtime
from ..behavior.response import ProcessedResponseSegment, process_response_async
from ..behavior.postprocess import postprocess_reply

TOOL_TIMEOUT = 15.0
REASONING_ONLY_HINT = "上一次只返回了思考内容，正文为空。请生成要发送的回复正文，不要只输出思考过程。"
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
    """在正式阶段边界执行回复生成，插件拒绝时不调用模型。"""
    return await execute_stage(
        "reply",
        hook_context(request, phase="reply") if request is not None else ChatHookContext("", phase="reply"),
        lambda: _generate(service, request, runtime, provider, messages, tools, allowed_names, epoch, limit),
        rejected=LLMResponse(content=""),
    )


async def _generate(
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
    data = response.provider_data or {}
    native_reasoning = data.get("client_type") == "openai_responses" and any(
        isinstance(item, dict) and item.get("type") == "reasoning" for item in data.get("output_items") or []
    )
    if not (response.content or "").strip() and (response.reasoning_content.strip() or native_reasoning):
        return await _continue_reasoning(provider, messages, response)
    return response


async def _continue_reasoning(provider: Any, messages: list[dict[str, Any]], response: LLMResponse) -> LLMResponse:
    """对齐 MaiBot：纯推理响应先续写，Provider 不支持时提示正文为空后重生成一次。"""
    logger.warning("[Chat] 回复模型只返回了思考内容，尝试续写一次")
    continuation = getattr(provider, "chat_continuation", None)
    if continuation is not None:
        try:
            continued = await asyncio.wait_for(
                continuation(messages=messages, previous=response, temperature=1.0, max_tokens=8192), 240
            )
        except Exception:
            logger.opt(exception=True).warning("[Chat] 推理续写失败，改用重生成")
            continued = None
        if continued is not None and (continued.content or "").strip():
            if continued.has_tool_calls:
                logger.warning("[Chat] 推理续写返回了工具调用，已丢弃该次输出")
                continued.tool_calls = []
                continued.tool_call_ids = []
            if not continued.reasoning_content:
                continued.reasoning_content = response.reasoning_content
            return continued
    trace_key = str(getattr(response, "_trace_key", "") or "")
    trace_kwargs = (
        {
            "_trace_merge": trace_key,
            "_trace_purpose": "replyer.reasoning_regeneration",
            "_trace_attempt": 2,
        }
        if trace_key
        else {}
    )
    retry = await asyncio.wait_for(
        provider.chat(
            messages=[*messages, {"role": "user", "content": REASONING_ONLY_HINT}],
            tools=None,
            temperature=1.0,
            max_tokens=8192,
            **trace_kwargs,
        ),
        240,
    )
    if not retry.has_tool_calls and not retry.reasoning_content:
        retry.reasoning_content = response.reasoning_content
    if retry.has_tool_calls:
        logger.warning("[Chat] 回复模型在重生成时返回了工具调用，已丢弃该次输出")
        return LLMResponse(content="", reasoning_content=response.reasoning_content)
    return retry


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
