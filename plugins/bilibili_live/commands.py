"""B 站直播订阅命令。"""

from __future__ import annotations

import re

from nonebot.adapters import Bot, Event
from nonebot.matcher import Matcher

from ...adapter import build_message, build_message_segment, extract_message_target
from ...adapter.support import event_group_id, event_user_id
from ...permission import PermLevel, PermScene
from . import P
from .client import BilibiliLiveError, LiveRoomSnapshot, fetch_room, parse_room_id
from .store import add_room, get_subscription, remove_room, set_room_state

subscribe = P.on_regex(
    r"^(?:#|＃|/)?(?:B站直播订阅|订阅B站直播|开启B站直播)(?:\s+.+)?$",
    name="bilibili_live_subscribe",
    display_name="订阅B站直播",
    priority=5,
    block=True,
    level=PermLevel.ADMIN,
    scene=PermScene.ALL,
)

unsubscribe = P.on_regex(
    r"^(?:#|＃|/)?(?:B站直播取消|取消B站直播|关闭B站直播)(?:\s+.+)?$",
    name="bilibili_live_unsubscribe",
    display_name="取消B站直播",
    priority=5,
    block=True,
    level=PermLevel.ADMIN,
    scene=PermScene.ALL,
)

subscription_list = P.on_regex(
    r"^(?:#|＃|/)?(?:B站直播订阅列表|B站直播列表|查询B站直播订阅)$",
    name="bilibili_live_list",
    display_name="B站直播订阅列表",
    priority=5,
    block=True,
    level=PermLevel.MEMBER,
    scene=PermScene.ALL,
)

room_query = P.on_regex(
    r"^(?:#|＃|/)?(?:B站直播查询|查询B站直播)(?:\s+.+)?$",
    name="bilibili_live_query",
    display_name="查询B站直播",
    priority=5,
    block=True,
    level=PermLevel.MEMBER,
    scene=PermScene.ALL,
)


def _event_text(event: Event) -> str:
    """提取事件消息文本。"""
    try:
        return str(event.get_message()).strip()
    except Exception:
        return ""


def _command_argument(event: Event, command_pattern: str) -> str:
    """提取命令后的直播间参数。"""
    match = re.match(
        rf"^(?:#|＃|/)?(?:{command_pattern})(?:\s+(.+))?$",
        _event_text(event),
        re.I,
    )
    return str(match.group(1) or "").strip() if match else ""


def _event_context(event: Event) -> tuple[str, str]:
    """返回当前群聊或私聊的订阅标识。"""
    group_id = event_group_id(event)
    if group_id is not None:
        return "group", str(group_id)
    return "private", event_user_id(event)


def _room_status(room: LiveRoomSnapshot) -> str:
    """构造直播间查询结果。"""
    status = "直播中" if room.is_live else "未开播"
    return "\n".join(
        [
            f"{room.name}（UID {room.uid}）",
            f"状态：{status}",
            f"标题：{room.title}",
            f"分区：{room.area}",
            f"直播间：{room.url}",
        ]
    )


async def _fetch_argument_room(event: Event, command_pattern: str) -> LiveRoomSnapshot:
    """解析命令参数并查询直播间。"""
    argument = _command_argument(event, command_pattern)
    if not argument:
        raise BilibiliLiveError("请在命令后填写直播间号或直播间链接")
    return await fetch_room(parse_room_id(argument))


@subscribe.handle()
async def _handle_subscribe(matcher: Matcher, event: Event) -> None:
    """订阅当前会话的直播间。"""
    try:
        room = await _fetch_argument_room(
            event,
            r"B站直播订阅|订阅B站直播|开启B站直播",
        )
    except BilibiliLiveError as exc:
        await matcher.finish(str(exc))

    sub_type, sub_key = _event_context(event)
    if not sub_key:
        await matcher.finish("无法识别当前会话，订阅失败")
    created = add_room(
        sub_type,
        sub_key,
        extract_message_target(event),
        room,
        event_user_id(event),
    )
    set_room_state(room.room_id, room.is_live)
    location = "本群" if sub_type == "group" else "当前私聊"
    if created:
        await matcher.finish(f"已为{location}订阅 {room.name} 的直播间 {room.room_id}")
    await matcher.finish(f"{location}已经订阅了 {room.name} 的直播间 {room.room_id}")


@unsubscribe.handle()
async def _handle_unsubscribe(matcher: Matcher, event: Event) -> None:
    """取消当前会话的直播间订阅。"""
    try:
        room = await _fetch_argument_room(
            event,
            r"B站直播取消|取消B站直播|关闭B站直播",
        )
    except BilibiliLiveError as exc:
        await matcher.finish(str(exc))

    sub_type, sub_key = _event_context(event)
    if not sub_key:
        await matcher.finish("无法识别当前会话，取消订阅失败")
    location = "本群" if sub_type == "group" else "当前私聊"
    if remove_room(sub_type, sub_key, room.room_id):
        await matcher.finish(f"已取消{location}对 {room.name} 直播间 {room.room_id} 的订阅")
    await matcher.finish(f"{location}没有订阅直播间 {room.room_id}")


@subscription_list.handle()
async def _handle_subscription_list(matcher: Matcher, event: Event) -> None:
    """列出当前会话的全部直播订阅。"""
    sub_type, sub_key = _event_context(event)
    if not sub_key:
        await matcher.finish("无法识别当前会话")
    subscription = get_subscription(sub_type, sub_key)
    rooms = subscription.get("rooms") if subscription else None
    if not isinstance(rooms, dict) or not rooms:
        await matcher.finish("当前会话没有 B 站直播订阅")

    lines = ["当前会话的 B 站直播订阅："]
    for index, record in enumerate(rooms.values(), start=1):
        if not isinstance(record, dict):
            continue
        name = str(record.get("name") or f"UID {record.get('uid') or '?'}")
        room_id = str(record.get("room_id") or "?")
        lines.append(f"{index}. {name} - {room_id}")
    await matcher.finish("\n".join(lines))


@room_query.handle()
async def _handle_room_query(matcher: Matcher, bot: Bot, event: Event) -> None:
    """查询直播间当前状态。"""
    try:
        room = await _fetch_argument_room(event, r"B站直播查询|查询B站直播")
    except BilibiliLiveError as exc:
        await matcher.finish(str(exc))

    segments = [build_message_segment(bot, "text", _room_status(room))]
    if room.cover:
        segments.append(build_message_segment(bot, "image", room.cover))
    await matcher.finish(build_message(bot, *segments))
