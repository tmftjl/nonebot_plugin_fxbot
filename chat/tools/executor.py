"""工具执行器。"""

from __future__ import annotations

import inspect
import json
from typing import Any

from nonebot import logger

from .registry import ToolRegistry, default_registry
from .runtime import ToolRuntime
from .types import ToolContext, ToolError


async def execute_tool(
    name: str,
    arguments: str | dict[str, Any],
    context: ToolContext,
    runtime: ToolRuntime,
    *,
    registry: ToolRegistry = default_registry,
) -> dict[str, Any]:
    """执行单个工具并返回模型可消费的结果。"""
    spec = registry.get(name)
    if spec is None:
        return {"ok": False, "error": f"工具不存在: {name}"}

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
