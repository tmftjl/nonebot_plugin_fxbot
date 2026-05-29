"""到期检查、自动退群和缓存刷新任务。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from nonebot import get_bots, logger
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_manager as get_config_manager
from ..db import with_session

from .guard import membership_guard
from .models import MembershipGroup, utc_now
from .service import membership_service


@dataclass
class MembershipTaskResult:
    """会员任务执行结果。"""

    left: int = 0
    expired: int = 0


class _MembershipTaskStore:
    """会员定时任务数据库操作。"""

    @with_session
    async def process(
        self,
        session: AsyncSession,
        groups: list[MembershipGroup],
        *,
        auto_leave: bool,
        delay: float,
    ) -> MembershipTaskResult:
        result = MembershipTaskResult()
        for group in groups:
            if group.status != "active" or group.expires_at is None:
                continue
            expires_at = _as_utc(group.expires_at)
            if expires_at is None:
                continue
            days = _days_remaining(expires_at)

            if days < 0:
                group_in_session = await session.get(MembershipGroup, group.id)
                if group_in_session is None:
                    continue
                group_in_session.status = "expired"
                group_in_session.expired_at = utc_now()
                group_in_session.updated_at = utc_now()
                result.expired += 1
                if auto_leave and await _leave_group(group.managed_by_bot, group.group_id):
                    result.left += 1
                if delay:
                    await asyncio.sleep(delay)
                continue

        return result


_task_store = _MembershipTaskStore()


def _as_utc(value: datetime | None) -> datetime | None:
    """将 datetime 转为 UTC aware 对象。"""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _days_remaining(expires_at: datetime) -> int:
    """计算剩余天数。"""
    delta = _as_utc(expires_at) - utc_now()  # type: ignore[operator]
    return delta.days


def _membership_cfg() -> dict[str, Any]:
    """读取会员任务配置。"""
    cfg = get_config_manager().get_system()
    return cfg["membership"]


async def _leave_group(bot_id: str | None, group_id: str) -> bool:
    """让 Bot 退出指定群。"""
    bot = get_bots().get(str(bot_id)) if bot_id else None
    if bot is None:
        return False
    try:
        if hasattr(bot, "set_group_leave"):
            await bot.set_group_leave(group_id=int(group_id))
            return True
    except Exception as exc:
        logger.warning(f"[MembershipTask] 群 {group_id} 退群失败: {exc}")
    return False


async def check_and_process_memberships() -> MembershipTaskResult:
    """检查会员到期状态，执行自动退群。"""
    cfg = _membership_cfg()
    auto_leave = bool(cfg["auto_leave_expired_groups"])
    delay = max(float(cfg["batch_delay_seconds"] or 0), 0.0)
    groups = await membership_service.list_groups()
    result = await _task_store.process(
        groups,
        auto_leave=auto_leave,
        delay=delay,
    )

    await membership_guard.reload_all_cache()
    return result


async def membership_task_job() -> MembershipTaskResult:
    """会员定时任务入口。"""
    result = await check_and_process_memberships()
    logger.info(
        f"[MembershipTask] 检查完成，过期={result.expired}，退群={result.left}"
    )
    return result


def setup_membership_tasks() -> None:
    """注册会员定时任务；缺少 scheduler 时跳过。"""
    try:
        from nonebot_plugin_apscheduler import scheduler
    except Exception:
        logger.warning("[MembershipTask] nonebot-plugin-apscheduler 未安装或未加载，跳过定时任务")
        return

    cfg = _membership_cfg()
    if not bool(cfg["enable_scheduler"]):
        return
    schedule_time = str(cfg["schedule_time"])
    try:
        hour_text, minute_text = schedule_time.split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
    except Exception:
        hour, minute = 12, 0

    scheduler.add_job(
        membership_task_job,
        trigger="cron",
        hour=hour,
        minute=minute,
        second=0,
        id="fxbot_membership_check",
        replace_existing=True,
    )
    logger.info(f"[MembershipTask] 定时任务已注册: {hour:02d}:{minute:02d}")
