"""长期记忆后台任务注册。"""

from __future__ import annotations

from nonebot import logger

from ..mood import behavior_aggregate_tick, behavior_attribute_tick
from .style import style_job_tick
from .config import memory_config
from .pipeline import memory_job_tick, backfill_job_tick
from .expression import expression_job_tick


def setup_memory_tasks() -> None:
    """注册批量抽取任务；缺少 scheduler 时跳过。"""
    try:
        from nonebot_plugin_apscheduler import scheduler
    except Exception:
        logger.warning("[ChatMemory] nonebot-plugin-apscheduler 未安装或未加载，跳过批量抽取任务")
        return
    interval = max(30, int(memory_config()["extract_interval_seconds"]))
    scheduler.add_job(
        memory_job_tick,
        trigger="interval",
        seconds=interval,
        id="fxbot_chat_memory_extract",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        backfill_job_tick,
        trigger="interval",
        seconds=10,
        id="fxbot_chat_memory_backfill",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        style_job_tick,
        trigger="interval",
        seconds=3600,
        id="fxbot_chat_style",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    expression_interval = max(60, interval)
    scheduler.add_job(
        expression_job_tick,
        trigger="interval",
        seconds=expression_interval,
        id="fxbot_chat_expression",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        behavior_attribute_tick,
        trigger="interval",
        seconds=60,
        id="fxbot_chat_behavior_attribute",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        behavior_aggregate_tick,
        trigger="cron",
        day_of_week="mon",
        hour=4,
        minute=0,
        id="fxbot_chat_behavior_aggregate",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    logger.info(
        f"[ChatMemory] 批量抽取任务已注册: 每 {interval} 秒；回填每 10 秒；风格每小时；"
        f"表达每 {expression_interval} 秒；行为归因每 60 秒、聚合每周一 04:00"
    )
