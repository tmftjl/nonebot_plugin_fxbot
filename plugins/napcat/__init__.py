"""NapCat 集成插件。"""

from __future__ import annotations

from typing import Any

from nonebot.adapters import Bot, Event
from nonebot.matcher import Matcher

from ...chat.tools import ToolContext, ToolRuntime, tool
from ...permission import PermLevel, PermScene
from ...plugin import Plugin
from . import image_display as image_display

P = Plugin("napcat", display_name="NapCat", enabled=True, level=PermLevel.LOW, scene=PermScene.ALL)

MAX_LIKE_TIMES = 10

like_cmd = P.on_regex(
    r"^#赞我",
    name="like",
    display_name="点赞",
    priority=5,
    block=True,
    level=PermLevel.LOW,
    scene=PermScene.ALL,
)


def _uid(event: Any) -> str:
    """提取用户 ID。"""
    if hasattr(event, "get_user_id"):
        try:
            return str(event.get_user_id())
        except Exception:
            pass
    return str(getattr(event, "user_id", "") or "")


async def _send_like(bot: Bot, user_id: str, times: int = MAX_LIKE_TIMES) -> bool:
    """调用 OneBot 点赞接口。"""
    if not hasattr(bot, "send_like"):
        return False
    await bot.send_like(user_id=int(user_id), times=max(1, min(times, MAX_LIKE_TIMES)))
    return True


@like_cmd.handle()
async def _handle_like(matcher: Matcher, bot: Bot, event: Event) -> None:
    """处理点赞命令。"""
    user_id = _uid(event)
    if not user_id:
        await matcher.finish("无法获取用户 ID")
    try:
        ok = await _send_like(bot, user_id)
    except Exception:
        ok = False
    await matcher.finish(f"已为你点赞 {MAX_LIKE_TIMES} 次" if ok else "当前适配器不支持点赞或今日已达上限")


@tool(
    name="like",
    description="为指定 QQ 用户点赞。仅 OneBot/NapCat 支持。",
    parameters={
        "type": "object",
        "properties": {
            "user_id": {"type": "string", "description": "目标 QQ 号"},
            "times": {"type": "integer", "description": "点赞次数，1 到 10"},
        },
        "required": ["user_id"],
    },
)
async def like_tool(ctx: ToolContext, rt: ToolRuntime, user_id: str, times: int = MAX_LIKE_TIMES) -> dict[str, Any]:
    """AI 工具：点赞。"""
    try:
        ok = await _send_like(rt.require_bot(), user_id, times)
    except Exception as exc:
        return {"success": False, "message": str(exc)}
    return {"success": ok, "message": "点赞完成" if ok else "当前适配器不支持点赞"}
