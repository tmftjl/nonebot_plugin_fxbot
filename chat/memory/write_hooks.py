"""学习落库前的统一拒绝型边界，回调不进入数据库事务。"""

from __future__ import annotations

from typing import Any
from collections.abc import Callable, Awaitable

from ..hooks import ChatHookContext
from ..stages import execute_stage


async def write_learning(tenant: str, kind: str, operation: Callable[[], Awaitable[bool]], **details: Any) -> bool:
    """插件可拒绝整次学习写入，不能伪造提交结果或推进游标。"""
    return await execute_stage(
        "learning_write",
        ChatHookContext(tenant, phase="learning"),
        operation,
        rejected=False,
        kind=kind,
        **details,
    )
