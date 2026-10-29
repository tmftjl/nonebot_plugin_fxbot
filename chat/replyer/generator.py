"""回复生成、工具循环与失败降级。

工具循环对齐 MaiBot（commit 062d45c，`src/chat/replyer/replyer_manager.py`），
发送前再按 `src/chat/utils/utils.py` 的后处理流程断句。
"""

from __future__ import annotations

import asyncio
from typing import Any

from nonebot import logger

from ..web import web_runtime
from ..tools import (
    SUBAGENT_TOOL_NAMES,
    SUBAGENT_STOP_TOOL_NAME,
    SUBAGENT_STATUS_TOOL_NAME,
    ToolContext,
    ToolRuntime,
    discovered,
    execute_tool,
    tool_allowed,
    tool_visible,
    default_registry,
    tool_result_content,
    prepare_tool_result_content,
)
from ..types import ChatRequest, is_proactive
from ..providers import LLMResponse
from ..capabilities import capabilities_config
from ..amemorix.runtime import amemorix_runtime
from ..behavior.response import ProcessedResponseSegment, process_response_async
from ..behavior.postprocess import postprocess_reply

MAX_TOOL_ROUNDS = 3
MAX_TOOL_CALLS_PER_ROUND = 4
MAX_TOOL_CALLS = 8
TOOL_TIMEOUT = 15.0
TOOL_BUDGET = 90.0
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


def finalize_response(response: LLMResponse) -> LLMResponse:
    """丢弃工具轮数用尽后残留的工具调用，避免写坏历史。"""
    if not response.has_tool_calls:
        return response
    response.tool_calls = []
    response.tool_call_ids = []
    response.content = response.content or "这次操作步骤太多，请换个说法再试。"
    return response


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
    allowed_specs = [spec for spec in specs if await tool_allowed(spec, runtime)]
    names = {spec.name for spec in allowed_specs}
    visible = {spec.name for spec in allowed_specs if tool_visible(spec, session_id)}
    tools = [tool for tool in default_registry.to_openai_tools() if tool["function"]["name"] in visible]
    return names, tools


def history_trace(trace: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """只把本轮实际处理的前几个调用写入历史，避免超大调用列表撑爆历史。

    工具轮附带的正文没有发送，写历史时清空，避免模型把自己没说过的话当成已说；
    Provider 原生数据同步丢弃，否则部分 Provider 会按原生块把这段正文带回去。
    """
    kept = trace[: MAX_TOOL_CALLS_PER_ROUND + 1]
    if len(kept) <= 1:
        return kept
    assistant = {
        **kept[0],
        "tool_calls": (kept[0].get("tool_calls") or [])[: len(kept) - 1],
        "content": "",
    }
    assistant.pop("provider_data", None)
    return [assistant, *kept[1:]]


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
    """调用回复模型并执行工具循环；会话失效后立即停止。"""
    session_id = request.session_id
    tools = visible_tools(allowed_names, session_id)
    response = await provider.chat(messages=messages, tools=tools or None)
    tool_calls_seen = 0
    deadline = asyncio.get_running_loop().time() + TOOL_BUDGET
    for round_index in range(MAX_TOOL_ROUNDS):
        if not service._alive(request, epoch) or not response.has_tool_calls:
            break
        trace = [response.to_message_dict()]
        context = ToolContext(
            user_id=request.user_id,
            group_id=request.group_id,
            session_id=request.session_id,
            platform=request.platform,
            proactive=is_proactive(request),
            metadata={**dict(request.metadata), "allowed_tools": sorted(allowed_names)},
        )
        previous_calls = tool_calls_seen
        tool_calls_seen += len(response.tool_calls)
        try:
            for index, tool_call in enumerate(response.tool_calls):
                func = tool_call.get("function", {})
                remaining = deadline - asyncio.get_running_loop().time()
                if not service._alive(request, epoch):
                    tool_result = {"ok": False, "error": "会话已失效"}
                elif index >= MAX_TOOL_CALLS_PER_ROUND:
                    tool_result = {"ok": False, "error": "超过单轮工具调用上限"}
                elif previous_calls + index >= MAX_TOOL_CALLS or remaining <= 0:
                    tool_result = {"ok": False, "error": "工具阶段预算已耗尽"}
                else:
                    try:
                        tool_result = await asyncio.wait_for(
                            execute_tool(
                                str(func.get("name") or ""),
                                func.get("arguments") or "{}",
                                context,
                                runtime,
                                allowed_names=allowed_names,
                            ),
                            timeout=min(TOOL_TIMEOUT, remaining),
                        )
                    except asyncio.TimeoutError:
                        tool_result = {"ok": False, "error": "工具执行超时"}
                    except Exception:
                        logger.opt(exception=True).warning("[Chat] 工具调用失败")
                        tool_result = {"ok": False, "error": "工具执行失败"}
                trace.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.get("id"),
                        "name": func.get("name"),
                        "content": await prepare_tool_result_content(
                            tool_result,
                            session_id=request.session_id,
                            tool_name=str(func.get("name") or ""),
                            arguments=func.get("arguments") or "{}",
                            timeout=min(TOOL_TIMEOUT, remaining),
                        ),
                    }
                )
        finally:
            for tool_call in response.tool_calls[len(trace) - 1 :]:
                trace.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.get("id"),
                        "name": tool_call.get("function", {}).get("name"),
                        "content": tool_result_content(
                            {"ok": False, "error": "工具调用已中断"}, session_id=request.session_id
                        ),
                    }
                )
            async with service.session_store.lock(request.session_id):
                if service._alive(request, epoch):
                    service.session_store.append_many(request.session_id, history_trace(trace), limit=limit)
        messages.extend(trace)
        tools = visible_tools(allowed_names, session_id)
        if not service._alive(request, epoch):
            break
        exhausted = (
            tool_calls_seen >= MAX_TOOL_CALLS
            or asyncio.get_running_loop().time() >= deadline
            or round_index == MAX_TOOL_ROUNDS - 1
        )
        response = await provider.chat(messages=messages, tools=None if exhausted else tools or None)
        if exhausted:
            break

    if response.has_tool_calls and service._alive(request, epoch):
        logger.warning(f"[Chat] 达到最大工具轮数，已丢弃残留工具调用: session={request.session_id}")
    return finalize_response(response)


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
