"""每日群聊日报定时任务。"""

from __future__ import annotations

import asyncio

from nonebot import logger, get_bots

from .config import get_config, is_enabled, auto_groups
from .report import generate, daily_period, group_name_for
from ...adapter import build_message, build_message_segment, send_message_to_target
from ...utils.tz import SHANGHAI_TZ


async def _send_report(bot, group_id: str) -> None:
    end, label, report_date = daily_period()
    result = await generate(
        bot,
        group_id,
        days=1,
        end=end,
        label=label,
        report_date=report_date,
        group_name=await group_name_for(bot, group_id),
    )
    if not result.ok:
        logger.info(f"[group_analysis] 群 {group_id} 自动日报跳过: {result.reason}")
        return
    target = {"group_id": group_id, "group_openid": group_id}
    message = build_message(bot, build_message_segment(bot, "image", result.image))
    await send_message_to_target(bot, target, message)


async def _scheduled_report() -> None:
    """逐个群生成前一天的日报；单群失败不影响后续群。"""
    if not is_enabled():
        return
    groups = auto_groups()
    bots = list(get_bots().values())
    if not groups or not bots:
        return
    for index, group_id in enumerate(groups):
        for bot in bots:
            try:
                await _send_report(bot, group_id)
                break
            except Exception:
                logger.opt(exception=True).warning(f"[group_analysis] 群 {group_id} 自动日报发送失败")
        if index + 1 < len(groups):
            await asyncio.sleep(3)


def _coerce_int(value: object, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def setup_group_analysis_tasks() -> None:
    """注册每日任务；缺少 scheduler 时跳过。"""
    try:
        from nonebot_plugin_apscheduler import scheduler
    except Exception:
        logger.warning("[group_analysis] nonebot-plugin-apscheduler 未安装或未加载，跳过定时任务")
        return
    config = get_config()
    hour = max(0, min(23, _coerce_int(config.get("auto_hour"), 8)))
    minute = max(0, min(59, _coerce_int(config.get("auto_minute"), 0)))
    scheduler.add_job(
        _scheduled_report,
        trigger="cron",
        hour=hour,
        minute=minute,
        timezone=SHANGHAI_TZ,
        id="group_analysis_daily",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    logger.info(f"[group_analysis] 定时任务已注册: 每天 {hour:02d}:{minute:02d} 生成前一天日报")


__all__ = ["setup_group_analysis_tasks"]
