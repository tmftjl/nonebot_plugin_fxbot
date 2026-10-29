"""聊天存储子系统的运行状态。"""

from __future__ import annotations

from nonebot import logger

_ready = False
_degraded_reason = ""


def mark_ready() -> None:
    """标记聊天存储可用。"""
    global _ready, _degraded_reason
    _ready = True
    _degraded_reason = ""


def mark_degraded(reason: str) -> None:
    """标记聊天存储降级并记录原因。"""
    global _ready, _degraded_reason
    _ready = False
    _degraded_reason = reason


def is_ready() -> bool:
    """聊天存储是否可用。"""
    return _ready


def degraded_reason() -> str:
    """返回降级原因；可用时为空串。"""
    return _degraded_reason


async def prepare_memory() -> None:
    """初始化聊天表结构；失败只降级，不影响聊天。"""
    from .migrate import ensure_schema

    try:
        await ensure_schema()
    except Exception as exc:
        mark_degraded(f"schema: {type(exc).__name__}: {exc}")
        logger.opt(exception=True).error("[ChatStorage] 表结构迁移失败，聊天存储降级")
    else:
        mark_ready()
