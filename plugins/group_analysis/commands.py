"""群聊日报手动命令。"""

from __future__ import annotations

import time

from nonebot import logger
from nonebot.params import RegexGroup
from nonebot.matcher import Matcher
from nonebot.adapters import Bot, Event

from . import P
from .config import bounded_days
from .report import REASON_TEXT, generate, is_running, manual_label, group_name_for
from ...adapter import build_message, event_group_id, build_message_segment
from ...utils.tz import today_str
from ...permission import PermLevel, PermScene
from ...chat.message_adapter import session_id_for_event

analysis_cmd = P.on_regex(
    r"^\s*[#＃/／]群聊分析(?:\s+(\d{1,2}))?\s*$",
    name="group_analysis_daily",
    display_name="群聊分析",
    priority=5,
    block=True,
    level=PermLevel.LOW,
    scene=PermScene.GROUP,
)


@analysis_cmd.handle()
async def _handle_analysis(bot: Bot, event: Event, matcher: Matcher, groups: tuple = RegexGroup()) -> None:
    group_id = event_group_id(event)
    if not group_id:
        await matcher.finish("请在群聊中使用 /群聊分析")
    if is_running(str(group_id)):
        await matcher.finish(REASON_TEXT["busy"])
    days = bounded_days(int(groups[0]) if groups and groups[0] else None)
    await matcher.send(f"正在统计{manual_label(days)}的群消息并生成日报，请稍候…")
    try:
        result = await generate(
            bot,
            str(group_id),
            days=days,
            end=int(time.time()),
            label=manual_label(days),
            report_date=today_str(),
            group_name=await group_name_for(bot, str(group_id)),
            session_id=session_id_for_event(bot, event),
        )
    except Exception:
        logger.opt(exception=True).warning(f"[group_analysis] 群 {group_id} 手动日报异常")
        await matcher.finish(REASON_TEXT["failed"])
    if not result.ok:
        await matcher.finish(REASON_TEXT.get(result.reason, REASON_TEXT["failed"]))
    await matcher.finish(build_message(bot, build_message_segment(bot, "image", result.image)))
