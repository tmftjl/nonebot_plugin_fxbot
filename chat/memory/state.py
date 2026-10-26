"""记忆子系统的运行状态。"""

from __future__ import annotations

from nonebot import logger

_ready = False
_degraded_reason = ""


def mark_ready() -> None:
    """标记记忆功能可用。"""
    global _ready, _degraded_reason
    _ready = True
    _degraded_reason = ""


def mark_degraded(reason: str) -> None:
    """标记记忆功能降级并记录原因。"""
    global _ready, _degraded_reason
    _ready = False
    _degraded_reason = reason


def is_ready() -> bool:
    """记忆功能是否可用。"""
    return _ready


def degraded_reason() -> str:
    """返回降级原因；可用时为空串。"""
    return _degraded_reason


async def prepare_memory() -> None:
    """初始化记忆表结构；失败只降级，不影响聊天。"""
    from .migrate import ensure_schema

    try:
        await ensure_schema()
    except Exception as exc:
        mark_degraded(f"schema: {type(exc).__name__}: {exc}")
        logger.opt(exception=True).error("[ChatMemory] 表结构迁移失败，记忆功能降级")
    else:
        mark_ready()
