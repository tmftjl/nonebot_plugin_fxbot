"""原生工具 Planner 的会话上下文、快速反应开关和等待提示。"""

from __future__ import annotations

from typing import Any
from dataclasses import dataclass

from ..tools import ToolRuntime
from ..types import ChatRequest
from ...config import get_manager as get_config_manager
from ..replyer.prompt import recall_query, assemble_messages, build_user_content
from ..context_summary import current_summary


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


def steering_enabled() -> bool:
    """快速反应模式：规划期间同一用户的新触发立即取消本次规划。"""
    try:
        timing = get_config_manager().get_system()["chat"].get("reply_timing")
    except Exception:
        return False
    return bool(timing.get("planner_message_steering")) if isinstance(timing, dict) else False


_WAIT_REMINDER_AFTER = 3
_consecutive_waits: dict[str, int] = {}


def wait_reminder(session_id: str) -> str:
    """连续等待后提醒规划器延长等待或结束会话。"""
    if _consecutive_waits.get(session_id, 0) < _WAIT_REMINDER_AFTER:
        return ""
    return "已经连续等待多次，为避免无限循环，请考虑延长等待或调用 stop 结束本轮。"


def note_plan(session_id: str, action: str) -> None:
    """记录规划动作：wait 累加，其他动作清零。"""
    if action == "wait":
        _consecutive_waits[session_id] = _consecutive_waits.get(session_id, 0) + 1
    else:
        _consecutive_waits.pop(session_id, None)


async def rebuild_messages(service: Any, context: PlanContext) -> list[dict[str, Any]]:
    """等待结束后重新读取历史，合并等待期间写入的普通消息。"""
    latest = context.request.metadata.pop("planner_latest_request", None)
    if latest is not None:
        request = context.request
        for key in (
            "user_id",
            "user_name",
            "message_id",
            "dedupe_key",
            "text",
            "images",
            "quoted_text",
            "reply_to_message_id",
            "occurred_at",
            "source_message_ids",
            "source_author_ids",
            "mentions",
        ):
            setattr(request, key, getattr(latest, key))
        for key in ("has_at_bot", "at_other"):
            request.metadata[key] = latest.metadata.get(key, False)
        request.metadata["sender_role"] = latest.metadata.get("sender_role")
        request.metadata["quote_snapshot"] = latest.metadata.get("quote_snapshot")
        context.current_content = build_user_content(latest)
    if runtime := context.request.metadata.pop("planner_latest_runtime", None):
        context.runtime = runtime
    async with service.session_store.lock(context.request.session_id):
        service.session_store.trim(context.request.session_id, context.limit)
        history = [
            message
            for message in service.session_store.get(context.request.session_id)
            if message.get("role") != "system"
        ]
        while history and history[0].get("role") != "user" and not history[0].get("_self_message"):
            history.pop(0)
    if latest is not None:
        from ..service import _memory_block

        context.memory_block = await _memory_block(context.request, recall_query(context.request, history))
    messages = assemble_messages(
        context.request,
        history,
        context.persona_text,
        context.memory_block,
        context.current_content,
        context_summary=current_summary(context.request.session_id),
    )
    from ..service import _learning_reference

    reference = await _learning_reference(context.request, history)
    if reference:
        messages.append({"role": "user", "content": reference})
    return messages
