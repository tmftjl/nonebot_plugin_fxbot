"""聊天学习与表情后台任务注册。"""

from __future__ import annotations

from nonebot import logger

from .style import style_job_tick
from ..emoji import emoji_config, emoji_cleanup_tick, emoji_reconcile_tick
from .cleanup import lifecycle_tick, expression_decay_tick
from .expression import expression_job_tick

_EXPRESSION_INTERVAL_SECONDS = 300


def setup_memory_tasks() -> None:
    """注册学习与表情任务；缺少 scheduler 时跳过。"""
    try:
        from nonebot_plugin_apscheduler import scheduler
    except Exception:
        logger.warning("[ChatStorage] nonebot-plugin-apscheduler 未安装或未加载，跳过聊天后台任务")
        return
    scheduler.add_job(
        style_job_tick,
        trigger="interval",
        seconds=3600,
        id="fxbot_chat_style",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        expression_job_tick,
        trigger="interval",
        seconds=_EXPRESSION_INTERVAL_SECONDS,
        id="fxbot_chat_expression",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        lifecycle_tick,
        trigger="cron",
        hour=4,
        minute=30,
        id="fxbot_chat_lifecycle",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        expression_decay_tick,
        trigger="cron",
        day_of_week="mon",
        hour=4,
        minute=20,
        id="fxbot_chat_expression_decay",
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
        f"[ChatStorage] 聊天任务已注册: 风格每小时；表达每 {_EXPRESSION_INTERVAL_SECONDS} 秒；"
        f"生命周期每日 04:30、表达衰减每周一 04:20；"
        f"表情补识别每 {check_minutes} 分钟、缓存每 {cleanup_hours:g} 小时"
    )
