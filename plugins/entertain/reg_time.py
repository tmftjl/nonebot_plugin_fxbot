"""QQ 注册时间查询。"""

from __future__ import annotations

import re
from datetime import datetime

import httpx
from nonebot import logger
from nonebot.adapters import Event
from nonebot.matcher import Matcher
from nonebot.params import RegexGroup

from ...permission import PermLevel, PermScene
from ...plugin import Plugin
from ...utils.http import get_shared_async_client
from .config import cfg_reg_time

P = Plugin("entertain", display_name="娱乐", enabled=True, level=PermLevel.LOW, scene=PermScene.ALL)

reg_time_cmd = P.on_regex(
    r"^#注册时间",
    name="query",
    display_name="注册时间",
    priority=5,
    block=True,
    level=PermLevel.MEMBER,
    scene=PermScene.ALL,
)


def _extract_at_user(event: Event) -> str | None:
    """从消息段提取 at 用户。"""
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


def _uid(event: Event) -> str:
    """提取用户 ID。"""
    if hasattr(event, "get_user_id"):
        try:
            return str(event.get_user_id())
        except Exception:
            pass
    return str(getattr(event, "user_id", "") or "")


async def _query_registration_time(qq: str) -> str | None:
    """调用注册时间接口。"""
    cfg = cfg_reg_time()
    api_url = str(cfg.get("qq_reg_time_api_url") or "").strip()
    api_key = str(cfg.get("qq_reg_time_api_key") or "").strip()
    if not api_url or not api_key:
        return None
    client = await get_shared_async_client()
    response = await client.get(api_url, params={"qq": qq, "key": api_key})
    response.raise_for_status()
    text = response.text.strip()
    return text if text and "注册时间" in text else None


def _build_registration_message(raw: str, qq: str) -> str:
    """构造旧版注册时间回复。"""
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    return "\n".join(
        [
            f"📌 查询QQ: {qq}",
            "══════════════",
            *lines,
            "══════════════",
            f"查询时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        ]
    )


@reg_time_cmd.handle()
async def _handle_reg_time(matcher: Matcher, event: Event, groups: tuple = RegexGroup()) -> None:
    """查询注册时间。"""
    raw = str(groups[0] if groups else "").strip()
    typed = re.search(r"\d{5,12}", raw)
    qq = _extract_at_user(event) or (typed.group(0) if typed else "") or _uid(event)
    if not qq:
        await matcher.finish("未指定查询目标")
    try:
        text = await _query_registration_time(qq)
    except httpx.HTTPError:
        logger.opt(exception=True).warning("[reg_time] 注册时间查询接口请求失败")
        await matcher.finish("服务暂不可用，请稍后重试")
    except Exception:
        logger.opt(exception=True).warning("[reg_time] 注册时间查询失败")
        await matcher.finish("查询失败，请检查配置或接口状态")
    if not text:
        await matcher.finish("查询失败，请检查账号有效性或API状态")
    await matcher.finish(_build_registration_message(text, qq))
