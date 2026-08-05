"""B 站直播状态检查与开播推送。"""

from __future__ import annotations

import asyncio
from typing import Any

from nonebot import get_bots, get_driver, logger

from ...adapter import build_message, build_message_segment, send_message_to_target
from .client import BilibiliLiveError, LiveRoomSnapshot, fetch_room
from .store import (
    get_room_records,
    get_room_state,
    get_room_subscriptions,
    set_room_state,
)

_startup_hook_registered = False
_background_task: asyncio.Task[None] | None = None
CHECK_INTERVAL_SECONDS = 60
MAX_CONCURRENCY = 4


def _room_id(record: dict[str, Any]) -> int:
    """从持久化记录中读取有效房间号。"""
    try:
        return int(record.get("room_id") or 0)
    except (TypeError, ValueError):
        return 0


async def _fetch_record(record: dict[str, Any], semaphore: asyncio.Semaphore) -> LiveRoomSnapshot | None:
    """查询单个订阅记录，失败时保留原状态。"""
    room_id = _room_id(record)
    if room_id <= 0:
        return None
    async with semaphore:
        try:
            return await fetch_room(room_id, known_name=str(record.get("name") or ""))
        except BilibiliLiveError as exc:
            logger.warning(f"[bilibili_live] 直播间 {room_id} 查询失败：{exc}")
            return None


async def _fetch_all_rooms() -> list[LiveRoomSnapshot]:
    """并发查询全部已订阅直播间。"""
    records = get_room_records()
    if not records:
        return []
    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
    results = await asyncio.gather(*(_fetch_record(record, semaphore) for record in records))
    return [result for result in results if result is not None]


def _push_text(room: LiveRoomSnapshot) -> str:
    """构造开播通知文本。"""
    lines = [
        f"[B站直播] {room.name} 开播了",
        f"标题：{room.title}",
        f"分区：{room.area}",
    ]
    if room.live_time and room.live_time != "0000-00-00 00:00:00":
        lines.append(f"开播时间：{room.live_time}")
    lines.append(room.url)
    return "\n".join(lines)


async def _send_to_subscription(subscription: dict[str, Any], room: LiveRoomSnapshot) -> bool:
    """使用可用 Bot 向一个持久化目标发送通知。"""
    target = subscription.get("target")
    if not isinstance(target, dict):
        return False
    for bot in get_bots().values():
        try:
            segments = [build_message_segment(bot, "text", _push_text(room))]
            if room.cover:
                segments.append(build_message_segment(bot, "image", room.cover))
            await send_message_to_target(bot, target, build_message(bot, *segments))
            return True
        except Exception:
            continue
    return False


async def _push_room(room: LiveRoomSnapshot) -> int:
    """向所有订阅该直播间的会话推送开播通知。"""
    subscriptions = get_room_subscriptions(room.room_id)
    pushed = 0
    for subscription in subscriptions:
        if await _send_to_subscription(subscription, room):
            pushed += 1
    logger.info(
        f"[bilibili_live] 直播间 {room.room_id} 开播推送完成：{pushed}/{len(subscriptions)} 个目标"
    )
    return pushed


async def calibrate_room_states() -> None:
    """启动时记录当前状态，不推送已经在播的直播间。"""
    rooms = await _fetch_all_rooms()
    for room in rooms:
        set_room_state(room.room_id, room.is_live)
    if rooms:
        logger.info(f"[bilibili_live] 已校准 {len(rooms)} 个直播间状态")


async def check_and_push() -> int:
    """检查直播状态，只推送离线到开播的变化。"""
    pushed = 0
    for room in await _fetch_all_rooms():
        previous = get_room_state(room.room_id)
        if previous is False and room.is_live:
            sent = await _push_room(room)
            pushed += sent
            if sent == 0:
                continue
        set_room_state(room.room_id, room.is_live)
    return pushed


async def _scheduled_check() -> None:
    """执行定时检查并隔离单轮异常。"""
    try:
        await check_and_push()
    except Exception:
        logger.opt(exception=True).warning("[bilibili_live] 直播状态检查失败")


async def _background_loop() -> None:
    """在插件自身任务中按固定间隔检查直播状态。"""
    while True:
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
        await _scheduled_check()


async def _startup_calibrate() -> None:
    """执行启动状态校准。"""
    try:
        await calibrate_room_states()
    except Exception:
        logger.opt(exception=True).warning("[bilibili_live] 启动状态校准失败")


async def _startup_task() -> None:
    """启动时校准状态并创建后台检查任务。"""
    global _background_task
    await _startup_calibrate()
    if _background_task is None or _background_task.done():
        _background_task = asyncio.create_task(_background_loop())
        logger.info(f"[bilibili_live] 后台检查任务已启动：每 {CHECK_INTERVAL_SECONDS} 秒检查一次")


async def _shutdown_task() -> None:
    """关闭插件后台检查任务。"""
    global _background_task
    task = _background_task
    _background_task = None
    if task is None or task.done():
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


def setup_bilibili_live_tasks() -> None:
    """注册直播轮询启动和关闭钩子。"""
    global _startup_hook_registered
    if not _startup_hook_registered:
        driver = get_driver()
        driver.on_startup(_startup_task)
        driver.on_shutdown(_shutdown_task)
        _startup_hook_registered = True
    logger.info("[bilibili_live] 已注册直播状态后台检查任务")
