"""远行商人命令。"""

from __future__ import annotations

from nonebot.adapters import Bot, Event
from nonebot.matcher import Matcher

from ...adapter import build_message, build_message_segment, extract_message_target
from ...adapter.support import event_group_id, event_user_id
from ...permission import PermLevel, PermScene
from . import P
from .client import fetch_merchant_snapshot
from .renderer import render_merchant_image
from .store import get_subscription, remove_subscription, upsert_subscription

merchant_query = P.on_regex(
    r"^[#＃]远行商人$",
    name="rocom_merchant_query",
    display_name="远行商人查询",
    priority=5,
    block=True,
    level=PermLevel.MEMBER,
    scene=PermScene.ALL,
)

merchant_subscribe = P.on_regex(
    r"^(?:#|＃|/)开启远行商人$",
    name="rocom_merchant_subscribe",
    display_name="订阅远行商人",
    priority=5,
    block=True,
    level=PermLevel.ADMIN,
    scene=PermScene.ALL,
)

merchant_unsubscribe = P.on_regex(
    r"^(?:#|＃|/)关闭远行商人$",
    name="rocom_merchant_unsubscribe",
    display_name="取消订阅远行商人",
    priority=5,
    block=True,
    level=PermLevel.ADMIN,
    scene=PermScene.ALL,
)

merchant_status = P.on_regex(
    r"^(?:#|＃|/)远行商人订阅$",
    name="rocom_merchant_subscription",
    display_name="远行商人订阅状态",
    priority=5,
    block=True,
    level=PermLevel.MEMBER,
    scene=PermScene.ALL,
)


def _event_context(event: Event) -> tuple[str, str]:
    """根据事件判断订阅类型和标识键。

    返回 (sub_type, sub_key):
        - 群聊: ("group", str(group_id))
        - 私聊: ("private", user_id)
    """
    group_id = event_group_id(event)
    if group_id is not None:
        return ("group", str(group_id))
    return ("private", event_user_id(event))


@merchant_query.handle()
async def _handle_query(matcher: Matcher, bot: Bot) -> None:
    """查询当前远行商人信息。"""
    try:
        snapshot = await fetch_merchant_snapshot()
        image = await render_merchant_image(snapshot)
    except Exception as exc:
        await matcher.finish(f"远行商人信息获取失败：{exc}")
    await matcher.finish(build_message(bot, build_message_segment(bot, "image", image)))


@merchant_subscribe.handle()
async def _handle_subscribe(matcher: Matcher, bot: Bot, event: Event) -> None:  # noqa: ARG001
    """订阅远行商人推送（群聊或私聊）。"""
    sub_type, sub_key = _event_context(event)
    if not sub_key:
        await matcher.finish("无法识别当前会话，订阅失败")
    upsert_subscription(sub_type, sub_key, extract_message_target(event), event_user_id(event))
    location = "本群" if sub_type == "group" else "私聊"
    await matcher.finish(f"已开启远行商人推送（{location}），将在远行商人数据更新时推送")


@merchant_unsubscribe.handle()
async def _handle_unsubscribe(matcher: Matcher, event: Event) -> None:
    """取消远行商人推送（群聊或私聊）。"""
    sub_type, sub_key = _event_context(event)
    if not sub_key:
        await matcher.finish("无法识别当前会话，取消订阅失败")
    removed = remove_subscription(sub_type, sub_key)
    location = "本群" if sub_type == "group" else "私聊"
    if removed:
        await matcher.finish(f"已取消远行商人推送（{location}）")
    else:
        await matcher.finish(f"当前{location}没有远行商人推送订阅")


@merchant_status.handle()
async def _handle_status(matcher: Matcher, event: Event) -> None:
    """查看远行商人推送订阅状态（群聊或私聊）。"""
    sub_type, sub_key = _event_context(event)
    if not sub_key:
        await matcher.finish("无法识别当前会话")
    sub = get_subscription(sub_type, sub_key)
    location = "本群" if sub_type == "group" else "该私聊"
    if not sub:
        await matcher.finish(f"{location}没有远行商人推送订阅")
    await matcher.finish(f"{location}已开启远行商人推送，远行商人数据更新时会推送")
