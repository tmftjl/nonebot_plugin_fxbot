"""NapCat 点赞功能。"""

from __future__ import annotations

import json
import asyncio
from typing import Any
from datetime import date

from nonebot.matcher import Matcher
from nonebot.adapters import Bot

from ...plugin import Plugin
from ...adapter import Uninfo, selfBot
from ...chat.tools import ToolContext, ToolRuntime, tool
from ...permission import PermLevel, PermScene
from ...utils.paths import data_dir

P = Plugin(
    "entertain",
    display_name="娱乐",
    enabled=True,
    level=PermLevel.LOW,
    scene=PermScene.ALL,
)

MAX_LIKE_TIMES = 10
_like_lock = asyncio.Lock()
LIKE_DATA_FILE = data_dir("entertain") / "like_daily.json"

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


def _load_like_data(today: str) -> dict[str, dict[str, dict[str, Any]]]:
    """加载当天点赞记录，自动丢弃旧日期。"""
    if not LIKE_DATA_FILE.exists():
        return {}
    try:
        raw = json.loads(LIKE_DATA_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    data: dict[str, dict[str, dict[str, Any]]] = {}
    for bot_id, users in raw.items():
        if not isinstance(users, dict):
            continue
        today_users = {
            str(user_id): record
            for user_id, record in users.items()
            if isinstance(record, dict) and record.get("date") == today
        }
        if today_users:
            data[str(bot_id)] = today_users
    return data


def _save_like_data(data: dict[str, dict[str, dict[str, Any]]]) -> None:
    """保存当天点赞记录。"""
    LIKE_DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    LIKE_DATA_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _get_like_count(data: dict[str, dict[str, dict[str, Any]]], bot_id: str, user_id: str) -> int:
    """获取机器人为用户记录的当天点赞次数。"""
    record = data.get(bot_id, {}).get(user_id)
    return int(record.get("count") or 0) if isinstance(record, dict) else 0


def _set_like_count(
    data: dict[str, dict[str, dict[str, Any]]], bot_id: str, user_id: str, today: str, count: int
) -> None:
    """写入机器人为用户记录的当天点赞次数。"""
    data.setdefault(bot_id, {})[user_id] = {"date": today, "count": count}


@like_cmd.handle()
async def _handle_like(matcher: Matcher, bot: Bot, session: Uninfo) -> None:
    """处理点赞命令。"""
    user_id = session.user.id
    if not user_id:
        await matcher.finish("无法获取用户 ID")
    bot_id = str(session.self_id)
    liked_on = date.today().isoformat()
    async with _like_lock:
        like_data = _load_like_data(liked_on)
        today_count = _get_like_count(like_data, bot_id, user_id)
        if today_count:
            await matcher.finish(f"今天已经为你点赞 {today_count} 次啦，明天再来吧！")

        count = await _send_likes_until_stopped(bot, user_id)
        if count == 0:
            return

        _set_like_count(like_data, bot_id, user_id, liked_on, count)
        _save_like_data(like_data)
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
async def like_tool(ctx: ToolContext, rt: ToolRuntime, user_id: str, times: int = MAX_LIKE_TIMES) -> dict[str, Any]:
    """AI 工具：点赞。"""
    try:
        ok = await _send_like(rt.require_bot(), user_id, times)
    except Exception as exc:
        return {"success": False, "message": str(exc)}
    return {"success": ok, "message": "点赞完成" if ok else "点赞未完成"}
