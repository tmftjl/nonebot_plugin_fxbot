"""聊天学习与表情后台任务注册。"""

from __future__ import annotations

from nonebot import logger

from ..emoji import emoji_config, emoji_cleanup_tick, emoji_reconcile_tick
from .cleanup import lifecycle_tick
from ...utils.tz import now
from ..work_schedule import refresh_calendar


def setup_memory_tasks() -> None:
    """注册学习与表情任务；缺少 scheduler 时跳过。"""
    try:
        from nonebot_plugin_apscheduler import scheduler
    except Exception:
        logger.warning("[ChatStorage] nonebot-plugin-apscheduler 未安装或未加载，跳过聊天后台任务")
        return
    scheduler.add_job(
        refresh_calendar,
        trigger="interval",
        minutes=1,
        next_run_time=now(),
        id="fxbot_chat_work_calendar",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        lifecycle_tick,
        trigger="interval",
        minutes=1,
        next_run_time=now(),
        id="fxbot_chat_lifecycle",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    emoji_settings = emoji_config()
    try:
        check_minutes = int(emoji_settings.get("check_interval", 10))
    except (TypeError, ValueError):
        check_minutes = 10
    scheduler.add_job(
        emoji_reconcile_tick,
        trigger="interval",
        seconds=max(60, check_minutes * 60),
        id="fxbot_chat_emoji_reconcile",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    cleanup_settings = emoji_settings.get("cache_cleanup") or {}
    try:
        cleanup_hours = float(cleanup_settings.get("check_interval_hours", 6))
    except (TypeError, ValueError):
        cleanup_hours = 6.0
    cleanup_seconds = max(60, int(cleanup_hours * 3600))
    scheduler.add_job(
        emoji_cleanup_tick,
        trigger="interval",
        seconds=cleanup_seconds,
        id="fxbot_chat_emoji_cleanup",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    logger.info(
        "[ChatStorage] 聊天任务已注册: 表达和黑话从裁切素材学习；"
        "诊断数据启动时及每分钟清理，保留 72 小时、每类全局最多 10000 条；"
        f"表情补识别每 {check_minutes} 分钟、缓存每 {cleanup_hours:g} 小时"
    )
