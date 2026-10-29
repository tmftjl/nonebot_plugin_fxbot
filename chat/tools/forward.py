"""view_forward_message：展开合并转发消息。"""

from __future__ import annotations

from typing import Any

from nonebot import logger

from .types import ToolError, ToolContext
from .runtime import ToolRuntime
from .registry import tool
from ...adapter import (
    UnsupportedCapability,
    event_message,
    extract_forward_ids,
    get_forward_messages,
    get_platform_adapter,
)

MAX_NODES = 100
MAX_CHARS = 4000


def _forward_ids(payload: Any) -> list[str]:
    """从 get_msg 结果或消息段列表里读转发 ID。"""
    message = payload.get("message") if isinstance(payload, dict) else payload
    return extract_forward_ids(message)


async def _root_id(runtime: ToolRuntime, msg_id: str) -> str:
    """定位要展开的转发；省略 msg_id 时用当前消息。"""
    bot = runtime.require_bot()
    if msg_id:
        try:
            ids = _forward_ids(await get_platform_adapter(bot).get_message(bot, msg_id))
        except UnsupportedCapability:
            return msg_id
        except Exception:
            logger.opt(exception=True).debug("[Chat] 读取消息失败，按转发 ID 直接展开")
            return msg_id
        return ids[0] if ids else msg_id
    ids = extract_forward_ids(event_message(runtime.require_event()))
    if not ids:
        raise ToolError("当前消息里没有合并转发；合并转发通常以卡片形式单独发送。")
    return ids[0]


def _path(value: Any) -> list[int]:
    """校验嵌套转发路径。"""
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise ToolError("path 必须是由非负整数组成的数组。")
    steps: list[int] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int) or item < 0:
            raise ToolError("path 必须是由非负整数组成的数组。")
        steps.append(item)
    return steps


async def _nodes_at(bot: Any, root_id: str, path: list[int]) -> list[dict[str, Any]]:
    """按 path 逐层展开，返回目标层的节点。"""
    nodes = await get_forward_messages(bot, root_id)
    for depth, step in enumerate(path):
        if step >= len(nodes):
            raise ToolError(f"path 第 {depth + 1} 个序号 {step} 超出范围，当前层共 {len(nodes)} 条。")
        nested = nodes[step].get("forwards") or []
        if not nested:
            raise ToolError(f"path 第 {depth + 1} 个序号指向的不是嵌套转发。")
        nodes = await get_forward_messages(bot, nested[0])
    return nodes


def _render(nodes: list[dict[str, Any]], path: list[int]) -> str:
    """按「昵称(ID)：正文」逐条列出，嵌套转发标注下一步 path。"""
    lines = [f"【合并转发消息：共 {len(nodes)} 条】"]
    used = 0
    for index, node in enumerate(nodes):
        if index >= MAX_NODES:
            lines.append(f"（超过 {MAX_NODES} 条，后面的消息未显示）")
            break
        name = str(node.get("name") or "未知用户")
        user_id = str(node.get("user_id") or "")
        who = f"{name}({user_id})" if user_id else name
        line = f"{index + 1}. {who}：{node.get('text') or '（空消息）'}"
        if node.get("forwards"):
            next_path = [*path, index]
            line += f"（含嵌套转发，用 path={next_path} 继续展开）"
        used += len(line)
        if used > MAX_CHARS:
            lines.append("（内容过长，已截断）")
            break
        lines.append(line)
    return "\n".join(lines)


@tool(
    name="view_forward_message",
    title="查看合并转发",
    description=(
        "读取合并转发消息的完整内容。消息里出现「[合并转发消息]」时用它展开；"
        "遇到嵌套转发时，按返回内容里的 path 再次调用继续展开。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "msg_id": {
                "type": "string",
                "description": "转发消息所在消息的 ID；省略时查看当前消息里的转发。",
            },
            "path": {
                "type": "array",
                "items": {"type": "integer", "minimum": 0},
                "description": "嵌套转发路径，使用上一次返回内容里的 path。",
            },
        },
    },
)
async def view_forward_message_tool(
    context: ToolContext,
    runtime: ToolRuntime,
    msg_id: str = "",
    path: Any = None,
) -> str:
    """展开转发的顶层节点，或按 path 展开嵌套层。"""
    del context
    steps = _path(path)
    root_id = await _root_id(runtime, str(msg_id or "").strip())
    try:
        nodes = await _nodes_at(runtime.require_bot(), root_id, steps)
    except ToolError:
        raise
    except UnsupportedCapability:
        raise ToolError("当前平台不支持读取合并转发消息。")
    except Exception:
        logger.opt(exception=True).warning("[Chat] 读取合并转发失败")
        raise ToolError("读取合并转发消息失败，可能是转发已过期。")
    if not nodes:
        raise ToolError("这条合并转发里没有消息。")
    return _render(nodes, steps)
