"""诊断数据保留与表达维护；单项失败不阻断其余步骤。"""

from __future__ import annotations

from typing import Any
from datetime import datetime, timedelta
from collections.abc import Callable, Awaitable

from nonebot import logger

from .state import is_ready
from .models import utc_now_naive
from .repository import DIAGNOSTIC_RETENTION_HOURS, chat_repository

_EXPRESSION_IDLE_DAYS = 30


async def _run(name: str, step: Callable[[], Awaitable[Any]], result: dict[str, Any]) -> None:
    try:
        result[name] = await step()
    except Exception:
        logger.opt(exception=True).warning(f"[ChatStorage] 生命周期步骤失败: {name}")


async def lifecycle_tick(*, now: datetime | None = None) -> dict[str, Any]:
    """清理规划样本与追踪，聊天历史、学习和长期记忆不参与清理。"""
    if not is_ready():
        return {}
    moment = now or utc_now_naive()
    result: dict[str, Any] = {}
    steps = {
        "traces": lambda: chat_repository.clear_traces(moment - timedelta(hours=DIAGNOSTIC_RETENTION_HOURS)),
        "planner_samples": lambda: chat_repository.clear_planner_samples(
            moment - timedelta(hours=DIAGNOSTIC_RETENTION_HOURS)
        ),
    }
    for name, step in steps.items():
        await _run(name, step, result)
    removed = sum(value for value in result.values() if isinstance(value, int))
    if removed:
        logger.info(f"[ChatStorage] 生命周期清理删除 {removed} 条: {result}")
    return result


async def expression_decay_tick(*, now: datetime | None = None) -> dict[str, int]:
    """每周衰减表达计数并驳回长期未用的低权重条目。"""
    if not is_ready():
        return {}
    moment = now or utc_now_naive()
    try:
        return await chat_repository.decay_expressions(moment, idle_days=_EXPRESSION_IDLE_DAYS)
    except Exception:
        logger.opt(exception=True).warning("[ChatExpression] 表达衰减失败")
        return {}
