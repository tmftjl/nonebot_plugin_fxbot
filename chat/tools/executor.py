"""工具执行器。"""

from __future__ import annotations

import json
import inspect
from typing import Any

from nonebot import logger

from .types import ToolSpec, ToolError, ToolContext
from .runtime import ToolRuntime
from .registry import ToolRegistry, default_registry
from ...permission import check_permission


async def tool_allowed(spec: ToolSpec, runtime: ToolRuntime) -> bool:
    """判断当前调用者是否有权使用该工具；无 bot/event 时按无权限处理。"""
    if not spec.permission:
        return True
    if runtime.bot is None or runtime.event is None:
        return False
    try:
        return await check_permission(spec.permission, runtime.bot, runtime.event)
    except Exception:
        logger.opt(exception=True).warning(f"[Tool] 权限检查失败: {spec.name}")
        return False


async def execute_tool(
    name: str,
    arguments: str | dict[str, Any],
    context: ToolContext,
    runtime: ToolRuntime,
    *,
    allowed_names: set[str],
    registry: ToolRegistry = default_registry,
) -> dict[str, Any]:
    """执行单个工具并返回模型可消费的结果。"""
    if name not in allowed_names:
        return {"ok": False, "error": f"工具未允许: {name}"}
    spec = registry.get(name)
    if spec is None:
        return {"ok": False, "error": f"工具不存在: {name}"}
    if not await tool_allowed(spec, runtime):
        return {"ok": False, "error": "权限不足，无法执行该操作"}

    try:
        args = json.loads(arguments) if isinstance(arguments, str) else dict(arguments or {})
    except json.JSONDecodeError as exc:
        return {"ok": False, "error": f"工具参数不是合法 JSON: {exc}"}

    try:
        if spec.args_model is not None:
            model = spec.args_model(**args)
            args = model.model_dump()
        result = spec.handler(context, runtime, **args)
        if inspect.isawaitable(result):
            result = await result
        return {"ok": True, "result": result}
    except ToolError as exc:
        return {"ok": False, "error": str(exc), "code": exc.code, "data": exc.data}
    except Exception as exc:
        logger.opt(exception=True).warning(f"[Tool] 工具 {name} 执行失败: {exc}")
        return {"ok": False, "error": "工具执行失败"}
