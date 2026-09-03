"""远行商人后台推送任务。"""

from __future__ import annotations

import asyncio

from nonebot import get_bots, get_driver, logger

from ...adapter import build_message, build_message_segment, send_message_to_target
from .client import SHANGHAI_TZ, fetch_merchant_snapshot
from .renderer import render_merchant_image
from .store import get_last_signature, get_subscriptions, set_last_signature

_startup_hook_registered = False
MERCHANT_RETRY_TIMES = 20
MERCHANT_RETRY_INTERVAL_SECONDS = 30


async def _send_to_subscription(subscription: dict, image: bytes) -> bool:
    target = subscription.get("target")
    if not isinstance(target, dict):
        return False
    for bot in get_bots().values():
        try:
            message = build_message(bot, build_message_segment(bot, "image", image))
            await send_message_to_target(bot, target, message)
            return True
        except Exception:
            continue
    return False


async def _push_snapshot(snapshot) -> int:
    """向所有订阅目标推送快照图片。"""
    subscriptions = get_subscriptions()
    if not subscriptions:
        return 0
    image = await render_merchant_image(snapshot)
    group_count = sum(1 for s in subscriptions if s.get("type", "group") == "group")
    private_count = sum(1 for s in subscriptions if s.get("type") == "private")
    logger.info(
        f"[rocom] 开始推送远行商人刷新: {group_count} 个群订阅, {private_count} 个私聊订阅"
    )
    pushed = 0
    for subscription in subscriptions:
        if await _send_to_subscription(subscription, image):
            pushed += 1
    logger.info(f"[rocom] 远行商人推送完成: {pushed}/{len(subscriptions)} 个目标")
    return pushed


async def calibrate_current_signature() -> None:
    """启动时校准当前快照签名，不触发推送。"""
    snapshot = await fetch_merchant_snapshot()
    set_last_signature(snapshot.signature)


async def check_and_push() -> bool:
    """检查远行商人变化并推送所有订阅。"""
    snapshot = await fetch_merchant_snapshot()
    last_signature = get_last_signature()
    if snapshot.signature == last_signature:
        return False

    if not snapshot.products:
        return False

    pushed = await _push_snapshot(snapshot)
    set_last_signature(snapshot.signature)
    if pushed:
        logger.info(f"[rocom] 已推送远行商人刷新：{pushed} 个目标")
    return True


async def check_and_push_with_retry() -> None:
    """刷新点后短重试，直到拿到新商品并推送。"""
    for index in range(MERCHANT_RETRY_TIMES):
        if await check_and_push():
            return
        if index + 1 < MERCHANT_RETRY_TIMES:
            await asyncio.sleep(MERCHANT_RETRY_INTERVAL_SECONDS)
    logger.info("[rocom] 远行商人刷新点检查结束：未发现新的可推送商品")


async def _scheduled_check() -> None:
    """定时检查远行商人刷新。"""
    try:
        await check_and_push_with_retry()
    except Exception:
        logger.opt(exception=True).warning("[rocom] 远行商人推送检查失败")


async def _startup_calibrate() -> None:
    """启动时记录当前签名，避免重启后误推旧数据。"""
    try:
        await calibrate_current_signature()
    except Exception:
        logger.opt(exception=True).warning("[rocom] 远行商人启动校准失败")


def setup_rocom_merchant_tasks() -> None:
    """注册远行商人推送任务；缺少 scheduler 时跳过。"""
    global _startup_hook_registered
    try:
        from nonebot_plugin_apscheduler import scheduler
    except Exception:
        logger.warning(
            "[rocom] nonebot-plugin-apscheduler 未安装或未加载，跳过定时任务"
        )
        return

    if not _startup_hook_registered:
        get_driver().on_startup(_startup_calibrate)
        _startup_hook_registered = True

    scheduler.add_job(
        _scheduled_check,
        trigger="cron",
        hour="8,12,16,20",
        minute=5,
        timezone=SHANGHAI_TZ,
        id="rocom_merchant_check",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    logger.info("[rocom] 定时任务已注册: 每日 08/12/16/20 点 05 分检查远行商人")
