"""每日生命周期清理与表达衰减；单项失败不阻断其余步骤。"""

from __future__ import annotations

from typing import Any
from datetime import datetime, timedelta
from collections.abc import Callable, Awaitable

from nonebot import logger

from .state import is_ready
from .models import utc_now_naive
from .repository import chat_repository

_TRACE_RETENTION_DAYS = 7
_EXPRESSION_IDLE_DAYS = 30


async def _run(name: str, step: Callable[[], Awaitable[Any]], result: dict[str, Any]) -> None:
    try:
        result[name] = await step()
    except Exception:
        logger.opt(exception=True).warning(f"[ChatStorage] 生命周期步骤失败: {name}")


async def lifecycle_tick(*, now: datetime | None = None) -> dict[str, Any]:
    """每日清理过期 trace；原始对话历史与 MaiBot 一样不按天删除。"""
    if not is_ready():
        return {}
    moment = now or utc_now_naive()
    result: dict[str, Any] = {}
    steps = {
        "traces": lambda: chat_repository.clear_traces(moment - timedelta(days=_TRACE_RETENTION_DAYS)),
    }
    for name, step in steps.items():
        await _run(name, step, result)
    logger.info(f"[ChatStorage] 生命周期清理完成: {result}")
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
