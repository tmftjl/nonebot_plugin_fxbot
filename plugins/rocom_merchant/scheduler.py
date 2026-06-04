"""远行商人后台推送任务。"""

from __future__ import annotations

from nonebot import get_bots, logger
from nonebot_plugin_apscheduler import scheduler

from ...adapter import build_message, build_message_segment, send_message_to_target
from .client import fetch_merchant_snapshot, snapshot_matches
from .config import cfg_merchant
from .renderer import render_merchant_image
from .store import get_last_signature, get_subscriptions, set_last_signature

_started_once = False


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


async def check_and_push() -> None:
    """检查远行商人变化并推送命中的订阅。"""
    global _started_once
    cfg = cfg_merchant()
    if not bool(cfg.get("enabled", True)):
        return
    subscriptions = get_subscriptions()
    if not subscriptions:
        return

    snapshot = await fetch_merchant_snapshot()
    last_signature = get_last_signature()
    if snapshot.signature == last_signature:
        return

    if not last_signature and not bool(cfg.get("push_on_start", False)) and not _started_once:
        set_last_signature(snapshot.signature)
        _started_once = True
        return

    image = await render_merchant_image(snapshot)
    pushed = 0
    for subscription in subscriptions:
        keywords = [str(item) for item in subscription.get("keywords") or []]
        if not snapshot_matches(snapshot, keywords):
            continue
        if await _send_to_subscription(subscription, image):
            pushed += 1
    set_last_signature(snapshot.signature)
    _started_once = True
    if pushed:
        logger.info(f"[rocom_merchant] 已推送远行商人刷新：{pushed} 个目标")


@scheduler.scheduled_job(
    "interval",
    seconds=max(60, int(cfg_merchant().get("check_interval_seconds") or 300)),
    id="rocom_merchant_check",
    coalesce=True,
    max_instances=1,
)
async def _scheduled_check() -> None:
    """定时检查远行商人刷新。"""
    try:
        await check_and_push()
    except Exception:
        logger.opt(exception=True).warning("[rocom_merchant] 远行商人推送检查失败")
