"""内置娱乐插件。"""

from __future__ import annotations

import random
from datetime import date
from typing import Any

from nonebot.adapters import Event
from nonebot.matcher import Matcher
from nonebot.params import RegexGroup

from ...permission import PermLevel, PermScene
from ...plugin import Plugin

P = Plugin("entertain", display_name="娱乐", enabled=True, level=PermLevel.LOW, scene=PermScene.ALL)

_FORTUNES = [
    ("大吉", "适合推进重要事项"),
    ("中吉", "保持节奏会有不错结果"),
    ("小吉", "适合整理计划和补齐细节"),
    ("平", "少做冲动决定"),
    ("小凶", "注意沟通和时间安排"),
]


def _uid(event: Any) -> str:
    """提取用户 ID。"""
    if hasattr(event, "get_user_id"):
        try:
            return str(event.get_user_id())
        except Exception:
            pass
    return str(getattr(event, "user_id", "") or "")


fortune_cmd = P.on_regex(
    r"^(?:#|/)?(?:今日运势|运势|抽签)$",
    name="today_fortune",
    display_name="今日运势",
    priority=5,
    block=True,
    level=PermLevel.LOW,
    scene=PermScene.ALL,
)


@fortune_cmd.handle()
async def _handle_fortune(matcher: Matcher, event: Event) -> None:
    """发送今日运势。"""
    seed = f"{date.today().isoformat()}:{_uid(event)}"
    rng = random.Random(seed)
    level, desc = rng.choice(_FORTUNES)
    lucky = rng.randint(1, 99)
    await matcher.finish(f"今日运势：{level}\n幸运值：{lucky}\n{desc}")


choose_cmd = P.on_regex(
    r"^(?:#|/)?(?:选择|帮我选)\s+(.+)$",
    name="choose",
    display_name="随机选择",
    priority=5,
    block=True,
    level=PermLevel.LOW,
    scene=PermScene.ALL,
)


@choose_cmd.handle()
async def _handle_choose(matcher: Matcher, groups: tuple = RegexGroup()) -> None:
    """随机选择一个选项。"""
    raw = str(groups[0] if groups else "").strip()
    options = [item.strip() for item in raw.replace("，", ",").replace("、", ",").split(",") if item.strip()]
    if len(options) < 2:
        await matcher.finish("请提供至少两个选项，用逗号分隔")
    await matcher.finish(f"我选：{random.choice(options)}")


dice_cmd = P.on_regex(
    r"^(?:#|/)?(?:骰子|roll)(?:\s+(\d+))?$",
    name="dice",
    display_name="骰子",
    priority=5,
    block=True,
    level=PermLevel.LOW,
    scene=PermScene.ALL,
)


@dice_cmd.handle()
async def _handle_dice(matcher: Matcher, groups: tuple = RegexGroup()) -> None:
    """掷骰子。"""
    sides = int(groups[0]) if groups and groups[0] else 6
    if sides < 2 or sides > 100000:
        await matcher.finish("骰子面数需要在 2 到 100000 之间")
    await matcher.finish(f"d{sides} = {random.randint(1, sides)}")
