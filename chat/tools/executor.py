"""工具执行器。"""

from __future__ import annotations

import json
import asyncio
import inspect
from typing import Any

from nonebot import logger

from .types import ToolSpec, ToolError, ToolContext
from ..hooks import ChatHookContext, guard, notify
from .runtime import ToolRuntime
from .registry import ToolRegistry, default_registry
from ...adapter import event_user_id, event_group_id
from ...permission import check_permission


async def tool_allowed(spec: ToolSpec, runtime: ToolRuntime, *, session_id: str = "") -> bool:
    """判断当前调用者是否有权使用该工具；无 bot/event 时按无权限处理。"""
    runtime_tools = getattr(runtime, "allowed_tools", None)
    if runtime_tools is not None and spec.name not in runtime_tools:
        return False
    identity = ChatHookContext(
        session_id=session_id,
        user_id=str(event_user_id(runtime.event) or "") if runtime.event is not None else "",
        group_id=event_group_id(runtime.event) if runtime.event is not None else None,
        phase="tool_discovery",
    )
    if not await guard("tool_guard", identity, tool_name=spec.name):
        return False
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
    if not await tool_allowed(spec, runtime, session_id=context.session_id):
        return {"ok": False, "error": "权限不足，无法执行该操作"}

    try:
        args = json.loads(arguments) if isinstance(arguments, str) else dict(arguments or {})
    except json.JSONDecodeError as exc:
        return {"ok": False, "error": f"工具参数不是合法 JSON: {exc}"}

    hook = ChatHookContext(
        session_id=context.session_id,
        user_id=context.user_id,
        group_id=context.group_id,
        platform=context.platform,
        phase="tool",
    )
    try:
        if spec.args_model is not None:
            model = spec.args_model(**args)
            args = model.model_dump()
        if not await guard("tool_guard", hook, tool_name=name, arguments=args):
            return {"ok": False, "error": "工具被会话策略拒绝"}
        await notify("tool_start", hook, tool_name=name, arguments=args)
        result = spec.handler(context, runtime, **args)
        if inspect.isawaitable(result):
            result = await result
        await notify("tool_complete", hook, tool_name=name, result=result)
        return {"ok": True, "result": result}
    except asyncio.CancelledError:
        await notify("tool_cancelled", hook, tool_name=name)
        raise
    except ToolError as exc:
        await notify("tool_error", hook, tool_name=name, code=exc.code)
        return {"ok": False, "error": str(exc), "code": exc.code, "data": exc.data}
    except Exception as exc:
        await notify("tool_error", ChatHookContext(context.session_id, phase="tool"), tool_name=name)
        logger.opt(exception=True).warning(f"[Tool] 工具 {name} 执行失败: {exc}")
        return {"ok": False, "error": "工具执行失败"}
