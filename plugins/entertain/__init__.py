"""内置娱乐插件。"""

from __future__ import annotations

import random
import re
from datetime import date, datetime
from typing import Any

import httpx
from nonebot import logger
from nonebot.adapters import Bot, Event
from nonebot.matcher import Matcher
from nonebot.params import RegexGroup

from ...permission import PermLevel, PermScene
from ...plugin import Plugin
from ...utils.compat import build_message, build_message_segment
from ...utils.http import get_shared_async_client
from .config import cfg_api_urls, cfg_reg_time
from . import musicshare as musicshare

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


def _extract_at_user(event: Event) -> str | None:
    """从消息段中提取第一个 at 目标。"""
    try:
        for segment in event.get_message():
            if getattr(segment, "type", "") == "at":
                data = getattr(segment, "data", {}) or {}
                user_id = data.get("qq") or data.get("user_id")
                if user_id:
                    return str(user_id)
    except Exception:
        return None
    return None


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


sick_cmd = P.on_regex(
    r"^(?:#|/)?发病语录$",
    name="get",
    display_name="发病语录",
    priority=5,
    block=True,
    level=PermLevel.LOW,
    scene=PermScene.ALL,
)


@sick_cmd.handle()
async def _handle_sick(matcher: Matcher, bot: Bot, event: Event) -> None:
    """获取发病语录。"""
    url = str(cfg_api_urls().get("sick_quote_api", "")).strip()
    if not url:
        await matcher.finish("未配置发病语录接口")
    try:
        client = await get_shared_async_client()
        response = await client.get(url)
        response.raise_for_status()
        data = response.json()
    except Exception:
        await matcher.finish("获取发病语录失败，请稍后重试")
    quote = str(data.get("message") or data.get("msg") or "").strip()
    if not quote:
        await matcher.finish("发病语录接口没有返回内容")
    user_id = _uid(event)
    await matcher.finish(
        build_message(
            bot,
            build_message_segment(bot, "at", user_id) if user_id else None,
            build_message_segment(bot, "text", f"\n{quote}" if user_id else quote),
        )
    )


reg_time_cmd = P.on_regex(
    r"^#注册时间\s*(.*)$",
    name="query",
    display_name="注册时间",
    priority=5,
    block=True,
    level=PermLevel.MEMBER,
    scene=PermScene.ALL,
)


async def _query_registration_time(qq: str) -> str | None:
    """调用接口查询 QQ 注册时间。"""
    cfg = cfg_reg_time()
    api_url = str(cfg.get("qq_reg_time_api_url", "")).strip()
    api_key = str(cfg.get("qq_reg_time_api_key", "")).strip()
    if not api_url or not api_key:
        return None
    client = await get_shared_async_client()
    response = await client.get(api_url, params={"qq": qq, "key": api_key})
    response.raise_for_status()
    text = response.text.strip()
    return text if text and "注册时间" in text else None


def _build_registration_message(raw: str, qq: str) -> str:
    """构造注册时间查询结果文本。"""
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    return "\n".join(
        [
            f"查询 QQ: {qq}",
            "==============",
            *lines,
            "==============",
            f"查询时间: {now}",
        ]
    )


@reg_time_cmd.handle()
async def _handle_reg_time(matcher: Matcher, event: Event, groups: tuple = RegexGroup()) -> None:
    """查询 QQ 注册时间。"""
    typed_raw = str(groups[0] if groups else "").strip()
    typed_match = re.search(r"\d{5,12}", typed_raw)
    typed = typed_match.group(0) if typed_match else ""
    qq = _extract_at_user(event) or typed or _uid(event)
    if not qq:
        await matcher.finish("未指定查询目标")
    try:
        text = await _query_registration_time(qq)
    except httpx.HTTPError:
        logger.opt(exception=True).warning("[entertain] 注册时间查询接口请求失败")
        await matcher.finish("服务暂不可用，请稍后重试")
    except Exception:
        logger.opt(exception=True).warning("[entertain] 注册时间查询失败")
        await matcher.finish("查询失败，请检查配置或接口状态")
    if not text:
        await matcher.finish("查询失败，请检查账号有效性或 API 配置")
    await matcher.finish(_build_registration_message(text, qq))
