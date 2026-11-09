"""中国工作日日历缓存及 AI 上班时段判断。"""

from __future__ import annotations

import json
import asyncio
from typing import Any
from datetime import date, datetime

from nonebot import logger

from ..config import get_manager as get_config_manager
from ..utils.tz import now, as_shanghai
from ..utils.http import get_shared_async_client
from ..utils.cache import cache
from ..utils.paths import data_dir
from .session_hooks import session_policy

CALENDAR_URL = "https://raw.githubusercontent.com/NateScarlet/holiday-cn/master/{year}.json"
_DOMAIN = "chat_work_calendar"


def validate_calendar(payload: Any, year: int) -> dict[str, bool]:
    """验证整份年度数据，避免下载失败或格式错误覆盖已有日历。"""
    if not isinstance(payload, dict) or payload.get("year") != year:
        raise ValueError("日历年份无效")
    if not isinstance(payload.get("papers"), list) or not payload["papers"]:
        raise ValueError("日历缺少公告来源")
    days = payload.get("days")
    if not isinstance(days, list) or not 1 <= len(days) <= 732:
        raise ValueError("日历日期列表无效")
    result: dict[str, bool] = {}
    for item in days:
        if not isinstance(item, dict) or not isinstance(item.get("isOffDay"), bool):
            raise ValueError("日历休息日标记无效")
        value = item.get("date")
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
            raise ValueError("日历日期无效")
        if not year - 1 <= date.fromisoformat(value).year <= year + 1 or value in result:
            raise ValueError("日历包含重复或越界日期")
        result[value] = item["isOffDay"]
    return result


def calendar_days(year: int) -> dict[str, bool] | None:
    """只读本地有效日历；消息处理路径绝不联网。"""
    key = (_DOMAIN, year)
    saved = cache.get(key)
    if saved is not None:
        return saved if isinstance(saved, dict) else None
    path = data_dir("ai_chat") / "work_calendar" / f"{year}.json"
    try:
        days = validate_calendar(json.loads(path.read_text(encoding="utf-8")), year)
    except FileNotFoundError:
        cache.set(key, False)
        return None
    except Exception:
        logger.opt(exception=True).warning("[ChatSchedule] 本地工作日日历不可用: year={}", year)
        cache.set(key, False)
        return None
    cache.set(key, days)
    return days


def schedule_status(chat: dict[str, Any] | None = None, *, at: datetime | None = None) -> dict[str, Any]:
    """按北京时间判断工作日和半开区间，17:30 起不再回复。"""
    chat = get_config_manager().get_system()["chat"] if chat is None else chat
    enabled = bool(chat.get("rest_time_no_reply", False))
    work_time = str(chat.get("work_time", "08:30-17:30"))
    result = {"enabled": enabled, "work_time": work_time, "allowed": True, "reason": "unrestricted"}
    if not enabled:
        return result
    current = as_shanghai(at) if at is not None else now()
    start, end = work_time.split("-")
    clock = current.strftime("%H:%M")
    if not start <= clock < end:
        return {**result, "allowed": False, "reason": "outside_work_time"}
    current_days = calendar_days(current.year)
    if current_days is None:
        return {**result, "allowed": False, "reason": "calendar_unavailable"}
    days = {**(calendar_days(current.year - 1) or {}), **current_days, **(calendar_days(current.year + 1) or {})}
    off = days.get(current.date().isoformat(), current.weekday() >= 5)
    return {**result, "allowed": not off, "reason": "rest_day" if off else "working"}


def rest_time_exempt(session_id: str | None) -> bool:
    """插件为该会话显式放行作息限制，例如服务器运维会话。"""
    return bool(session_id) and session_policy(session_id).allow_rest_time


def can_reply(chat: dict[str, Any] | None = None, *, session_id: str | None = None) -> bool:
    """手动总开关优先，日历规则不改写配置也不自动打开手动关闭的 AI。"""
    chat = get_config_manager().get_system()["chat"] if chat is None else chat
    if not bool(chat.get("enabled", True)):
        return False
    if rest_time_exempt(session_id):
        return True
    return bool(schedule_status(chat)["allowed"])


def work_life_prompt(chat: dict[str, Any], *, session_id: str | None = None) -> str:
    """作息开关启用时为 Planner 和回复器提供一致的生活背景。"""
    if not chat.get("rest_time_no_reply", False) or rest_time_exempt(session_id):
        return ""
    work_time = str(chat.get("work_time", "08:30-17:30"))
    return (
        "[日常作息]\n"
        f"北京时间 {work_time}，仅法定工作日（含调休补班）上班时在线聊天；"
        "休息时忙着打游戏、看电视、出去玩，不上线回复。被问时自然解释，不反复强调或编造具体经历。"
    )


async def refresh_calendar() -> None:
    """启动、启用及每日更新相邻年度，失败保留最近一次有效文件。"""
    if not get_config_manager().get_system()["chat"].get("rest_time_no_reply", False):
        return
    year = now().year
    refresh_key = (_DOMAIN, "refresh", year)
    if cache.get(refresh_key):
        return
    updated = False
    client = await get_shared_async_client()
    for target in (year, year - 1, year + 1):
        try:
            response = await client.get(CALENDAR_URL.format(year=target), timeout=10, follow_redirects=True)
            if response.status_code == 404 and target != year:
                continue
            response.raise_for_status()
            payload = response.json()
            days = validate_calendar(payload, target)
            root = data_dir("ai_chat") / "work_calendar"
            root.mkdir(parents=True, exist_ok=True)
            path = root / f"{target}.json"
            temporary = path.with_suffix(".json.tmp")
            text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
            await asyncio.to_thread(temporary.write_text, text, encoding="utf-8")
            await asyncio.to_thread(temporary.replace, path)
            cache.set((_DOMAIN, target), days)
            updated = updated or target == year
            logger.info("[ChatSchedule] 工作日日历已更新: year={}", target)
        except Exception:
            logger.opt(exception=True).warning("[ChatSchedule] 更新日历失败，保留本地缓存: year={}", target)
    cache.set(refresh_key, True, ttl=86400 if updated else 900)
