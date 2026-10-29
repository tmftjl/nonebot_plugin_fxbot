"""规划器调用与动作分派。

结构参照 MaiBot（commit 062d45c，`src/chat/heart_flow/`）：先判断本轮说什么，
必要时等待或调用工具，再交给回复模型。
"""

from __future__ import annotations

import json
import asyncio
from uuid import uuid4
from typing import Any
from contextlib import suppress
from dataclasses import dataclass

from nonebot import logger

from ..emoji import emoji_config, emoji_catalog, emoji_collage
from ..tools import ToolContext, ToolRuntime, execute_tool
from ..types import ChatRequest, is_proactive
from ...config import get_manager as get_config_manager
from ..planner import Plan, request_plan, plan_features, planner_config, record_plan_sample
from ..tools.result import prepare_tool_result_content
from ..replyer.prompt import assemble_messages
from ..context_summary import current_summary
from ..providers.routing import fallback_provider
from ..replyer.generator import TOOL_TIMEOUT
from ..message_receive.history import end_turn_without_reply


@dataclass
class PlanContext:
    """规划动作执行所需的本轮上下文。"""

    request: ChatRequest
    messages: list[dict[str, Any]]
    entry: dict[str, Any]
    epoch: int
    runtime: ToolRuntime
    allowed_names: set[str]
    persona_text: str
    memory_block: str
    current_content: Any
    limit: int
    provider_name: str


@dataclass
class PlanOutcome:
    """动作执行结果；finished 表示本轮结束，pause 表示让出轮次等待后再重新规划。"""

    finished: bool = False
    messages: list[dict[str, Any]] | None = None
    pause: float = 0.0


def steering_enabled() -> bool:
    """快速反应模式：规划期间同一用户的新触发立即取消本次规划。"""
    try:
        timing = get_config_manager().get_system()["chat"].get("reply_timing")
    except Exception:
        return False
    return bool(timing.get("planner_message_steering")) if isinstance(timing, dict) else False


async def request_plan_with_steering(
    planner: Any,
    messages: list[dict[str, Any]],
    tools: list[Any],
    *,
    default_wait: int,
    hint: str,
    alive: Any = None,
    emoji_image: str | None = None,
    emoji_hashes: list[str] | None = None,
) -> Plan | None:
    """规划请求；轮次失效时取消在途请求，交给新触发重新规划。"""
    options = {
        "default_wait": default_wait,
        "hint": hint,
        "emoji_image": emoji_image,
        "emoji_hashes": emoji_hashes,
    }
    if alive is None or not steering_enabled():
        return await request_plan(planner, messages, tools, **options)
    task = asyncio.create_task(request_plan(planner, messages, tools, **options))
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=0.2)
            if task in done:
                return task.result()
            if not alive():
                logger.debug("[ChatPlanner] 规划期间出现新触发，取消本次规划")
                return None
    finally:
        if not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


_WAIT_REMINDER_AFTER = 3
_consecutive_waits: dict[str, int] = {}


def wait_reminder(session_id: str) -> str:
    """连续等待后提醒规划器延长等待或结束会话。"""
    if _consecutive_waits.get(session_id, 0) < _WAIT_REMINDER_AFTER:
        return ""
    return "已经连续等待多次，为避免无限循环会话，请考虑延长 wait_seconds 或选择 end_conversation 结束本轮。"


def note_plan(session_id: str, action: str) -> None:
    """记录规划动作：wait 累加，其他动作清零。"""
    if action == "wait":
        _consecutive_waits[session_id] = _consecutive_waits.get(session_id, 0) + 1
    else:
        _consecutive_waits.pop(session_id, None)


async def maybe_plan(
    request: ChatRequest,
    messages: list[dict[str, Any]],
    tools: list[Any],
    *,
    history_len: int = 0,
    memory_chars: int = 0,
    alive: Any = None,
) -> Plan | None:
    """复杂模式下调用一次规划器；失败时返回 None，回退普通回复。"""
    config = planner_config()
    if config.get("mode") != "complex":
        return None
    try:
        hint = wait_reminder(request.session_id)
        emoji_image: str | None = None
        emoji_hashes: list[str] | None = None
        if emoji_config().get("use_new_send_logic"):
            catalog = await emoji_catalog(request.session_id)
            if catalog:
                emoji_image = await asyncio.to_thread(emoji_collage, catalog)
                if emoji_image:
                    emoji_hashes = [row.hash for row in catalog]
        async with fallback_provider("planner", request.session_id) as planner:
            plan = await request_plan_with_steering(
                planner,
                messages,
                tools,
                default_wait=int(config.get("wait_seconds", 60)),
                hint=hint,
                alive=alive,
                emoji_image=emoji_image,
                emoji_hashes=emoji_hashes,
            )
    except Exception:
        logger.opt(exception=True).warning("[ChatPlanner] 规划失败，回退普通回复")
        return None
    if plan is None:
        logger.debug("[ChatPlanner] 规划结果无法解析，回退普通回复")
        _consecutive_waits.pop(request.session_id, None)
        return None
    note_plan(request.session_id, plan.action)
    features = plan_features(request, history_len=history_len, memory_chars=memory_chars)
    await record_plan_sample(
        request,
        plan,
        features,
        provider=str(getattr(planner, "provider_id", "")),
        model=str(getattr(planner, "get_model", lambda: "")()),
    )
    return plan


async def dispatch_plan(service: Any, context: PlanContext, plan: Plan) -> PlanOutcome:
    """按动作分派；text_reply 与 emoji_reply 继续走回复模型。"""
    handlers = {
        "no_reply": plan_silent,
        "end_conversation": plan_silent,
        "wait": plan_wait,
        "tool": plan_tool,
    }
    handler = handlers.get(plan.action)
    if handler is None:
        return PlanOutcome()
    return await handler(service, context, plan)


async def plan_silent(service: Any, context: PlanContext, plan: Plan) -> PlanOutcome:
    """保持安静：结束本轮，不生成也不发送。"""
    await end_turn_without_reply(context.request, context.provider_name)
    return PlanOutcome(finished=True)


async def plan_wait(_service: Any, context: PlanContext, plan: Plan) -> PlanOutcome:
    """先让出轮次等待；醒后重读新消息重新规划，而不是直接回复。"""
    return PlanOutcome(pause=max(0.0, float(plan.wait_seconds)))


async def plan_tool(service: Any, context: PlanContext, plan: Plan) -> PlanOutcome:
    """先执行规划器选定的工具，再把结果交给回复模型。"""
    tool_context = ToolContext(
        user_id=context.request.user_id,
        group_id=context.request.group_id,
        session_id=context.request.session_id,
        platform=context.request.platform,
        proactive=is_proactive(context.request),
        metadata={**dict(context.request.metadata), "allowed_tools": sorted(context.allowed_names)},
    )
    arguments = json.dumps(plan.arguments, ensure_ascii=False, default=str)
    try:
        result = await asyncio.wait_for(
            execute_tool(
                plan.tool,
                arguments,
                tool_context,
                context.runtime,
                allowed_names=context.allowed_names,
            ),
            timeout=TOOL_TIMEOUT,
        )
    except asyncio.TimeoutError:
        result = {"ok": False, "error": "工具执行超时"}
    except Exception:
        logger.opt(exception=True).warning("[ChatPlanner] 规划工具调用失败")
        result = {"ok": False, "error": "工具执行失败"}
    call_id = f"plan-{uuid4().hex}"
    call = {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": plan.tool, "arguments": arguments},
            }
        ],
    }
    tool_message = {
        "role": "tool",
        "tool_call_id": call_id,
        "name": plan.tool,
        "content": await prepare_tool_result_content(
            result,
            session_id=context.request.session_id,
            tool_name=plan.tool,
            arguments=arguments,
            timeout=TOOL_TIMEOUT,
        ),
    }
    return PlanOutcome(messages=[*context.messages, call, tool_message])


async def rebuild_messages(service: Any, context: PlanContext) -> list[dict[str, Any]]:
    """等待结束后重新读取历史，合并等待期间写入的普通消息。"""
    async with service.session_store.lock(context.request.session_id):
        service.session_store.trim(context.request.session_id, context.limit)
        history = [
            message
            for message in service.session_store.get(context.request.session_id)
            if message.get("role") != "system" and message is not context.entry
        ]
        while history and history[0].get("role") != "user":
            history.pop(0)
    return assemble_messages(
        context.request,
        history,
        context.persona_text,
        context.memory_block,
        context.current_content,
        context_summary=current_summary(context.request.session_id),
    )
