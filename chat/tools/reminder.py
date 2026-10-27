"""提醒创建工具。"""

from __future__ import annotations

from .types import ToolError, ToolContext
from .runtime import ToolRuntime
from .registry import tool
from ..reminders import create, reminder_config, reminder_prompt


@tool(
    name="set_reminder",
    title="设置提醒",
    description="在指定时间提醒当前会话。用户明确要求提醒时使用；时间必须是未来时间。",
    parameters={
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "提醒内容，简洁明确"},
            "due_at": {
                "type": "string",
                "description": "提醒时间，ISO 8601；例如 2026-10-28T09:00:00+08:00",
            },
        },
        "required": ["content", "due_at"],
    },
)
async def set_reminder_tool(
    context: ToolContext, runtime: ToolRuntime, content: str, due_at: str
) -> str:
    """创建提醒，并返回需要用户确认或已生效的说明。"""
    if not reminder_config().get("enabled"):
        raise ToolError("提醒功能未启用")
    if not context.session_id:
        raise ToolError("无法识别当前会话，暂时不能设置提醒")
    try:
        row, created = await create(context.session_id, context.user_id, content, due_at)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    prefix = "提醒已创建：" if created else "这条提醒已经存在："
    return prefix + reminder_prompt(row)
