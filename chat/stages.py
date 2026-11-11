"""核心阶段的统一守卫与生命周期执行器，不开放可写运行时。"""

from __future__ import annotations

import asyncio
from typing import Any, Literal, TypeVar, get_args
from collections.abc import Callable, Awaitable

from .hooks import ChatHookContext, guard, notify

T = TypeVar("T")
StageName = Literal["memory_retrieval", "learning_reference", "learning_write", "scheduled_reply", "planner", "reply"]
CHAT_STAGES = frozenset(get_args(StageName))


async def execute_stage(
    stage: StageName,
    context: ChatHookContext,
    operation: Callable[[], Awaitable[T]],
    *,
    rejected: T,
    **details: Any,
) -> T:
    """拒绝时不创建操作协程，通知失败隔离，取消与核心异常保持原样传播。"""
    if stage not in CHAT_STAGES:
        raise ValueError("未定义的聊天阶段")
    if {"result", "event", "hook_kind"}.intersection(details):
        raise ValueError("阶段详情不能包含保留字段 result、event 或 hook_kind")
    if not await guard("stage_guard", context, stage=stage, **details):
        await notify("stage_rejected", context, stage=stage, **details)
        return rejected
    try:
        await notify("stage_start", context, stage=stage, **details)
        result = await operation()
    except asyncio.CancelledError:
        await notify("stage_cancelled", context, stage=stage)
        raise
    except Exception:
        await notify("stage_error", context, stage=stage)
        raise
    await notify("stage_complete", context, stage=stage, result=result, **details)
    return result
