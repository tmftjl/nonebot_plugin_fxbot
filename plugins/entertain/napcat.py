"""NapCat 点赞功能。"""

from __future__ import annotations

from typing import Any

from nonebot.adapters import Bot
from nonebot.matcher import Matcher

from ...adapter import selfBot
from ...adapter.uninfo import Uninfo
from ...chat.tools import ToolContext, ToolRuntime, tool
from ...permission import PermLevel, PermScene
from ...plugin import Plugin

P = Plugin(
    "entertain",
    display_name="娱乐",
    enabled=True,
    level=PermLevel.LOW,
    scene=PermScene.ALL,
)

MAX_LIKE_TIMES = 10

like_cmd = P.on_regex(
    r"^[#＃]赞我",
    name="like",
    display_name="点赞",
    priority=5,
    block=True,
    level=PermLevel.LOW,
    scene=PermScene.ALL,
)


async def _send_like(bot: Bot, user_id: str, times: int = MAX_LIKE_TIMES) -> bool:
    """调用 OneBot 点赞接口。"""
    await selfBot.like(user_id, max(1, min(times, MAX_LIKE_TIMES)))
    return True


async def _send_likes_until_stopped(bot: Bot, user_id: str) -> int:
    """每次发送最多十个赞，直到接口拒绝或当前适配器不支持。"""
    count = 0
    while True:
        try:
            await selfBot.like(user_id, MAX_LIKE_TIMES)
        except Exception:
            break
        count += MAX_LIKE_TIMES
    return count


@like_cmd.handle()
async def _handle_like(matcher: Matcher, bot: Bot, session: Uninfo) -> None:
    """处理点赞命令。"""
    user_id = session.user.id
    if not user_id:
        await matcher.finish("无法获取用户 ID")
    count = await _send_likes_until_stopped(bot, user_id)
    if count == 0:
        return
    await matcher.finish(f"已为你点赞 {count} 次，记得回我哟！")


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
async def like_tool(
    ctx: ToolContext, rt: ToolRuntime, user_id: str, times: int = MAX_LIKE_TIMES
) -> dict[str, Any]:
    """AI 工具：点赞。"""
    try:
        ok = await _send_like(rt.require_bot(), user_id, times)
    except Exception as exc:
        return {"success": False, "message": str(exc)}
    return {"success": ok, "message": "点赞完成" if ok else "点赞未完成"}
