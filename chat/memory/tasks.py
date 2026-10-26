"""长期记忆后台任务注册。"""

from __future__ import annotations

from nonebot import logger

from .config import memory_config
from .pipeline import memory_job_tick


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
    logger.info(f"[ChatMemory] 批量抽取任务已注册: 每 {interval} 秒")
