"""远行商人命令。"""

from __future__ import annotations

import re

from nonebot.adapters import Bot, Event
from nonebot.matcher import Matcher
from nonebot.params import RegexGroup

from ...adapter import build_message, build_message_segment, event_group_id, event_user_id, extract_message_target
from ...permission import PermLevel, PermScene
from . import P
from .client import fetch_merchant_snapshot
from .config import cfg_merchant
from .renderer import render_merchant_image
from .store import get_subscription, remove_subscription, upsert_subscription

merchant_query = P.on_regex(
    r"^远行商人$",
    name="rocom_merchant_query",
    display_name="远行商人查询",
    priority=5,
    block=True,
    level=PermLevel.MEMBER,
    scene=PermScene.GROUP,
)

merchant_subscribe = P.on_regex(
    r"^(?:#|/)开启远行商人\s*(.*)$",
    name="rocom_merchant_subscribe",
    display_name="订阅远行商人",
    priority=5,
    block=True,
    level=PermLevel.ADMIN,
    scene=PermScene.GROUP,
)

merchant_unsubscribe = P.on_regex(
    r"^(?:#|/)关闭远行商人$",
    name="rocom_merchant_unsubscribe",
    display_name="取消订阅远行商人",
    priority=5,
    block=True,
    level=PermLevel.ADMIN,
    scene=PermScene.GROUP,
)

merchant_status = P.on_regex(
    r"^(?:#|/)远行商人订阅$",
    name="rocom_merchant_subscription",
    display_name="远行商人订阅状态",
    priority=5,
    block=True,
    level=PermLevel.MEMBER,
    scene=PermScene.GROUP,
)


def _group_key(event: Event) -> str:
    group_id = event_group_id(event)
    return str(group_id or "")


def _parse_keywords(raw: str) -> list[str]:
    values = [item.strip() for item in re.split(r"[\s,，、]+", raw or "") if item.strip()]
    if values:
        return values
    defaults = cfg_merchant().get("default_keywords") or []
    return [str(item).strip() for item in defaults if str(item).strip()]


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
async def _handle_subscribe(matcher: Matcher, bot: Bot, event: Event, args: tuple[str, ...] = RegexGroup()) -> None:  # noqa: ARG001
    """订阅本群远行商人推送。"""
    group_key = _group_key(event)
    if not group_key:
        await matcher.finish("只能在群聊中订阅远行商人推送")
    keywords = _parse_keywords(args[0] if args else "")
    upsert_subscription(group_key, extract_message_target(event), keywords, event_user_id(event))
    label = "、".join(keywords) if keywords else "全部商品刷新"
    await matcher.finish(f"已订阅远行商人推送：{label}")


@merchant_unsubscribe.handle()
async def _handle_unsubscribe(matcher: Matcher, event: Event) -> None:
    """取消本群远行商人推送。"""
    group_key = _group_key(event)
    if not group_key:
        await matcher.finish("只能在群聊中取消订阅")
    removed = remove_subscription(group_key)
    await matcher.finish("已取消远行商人推送" if removed else "本群没有远行商人推送订阅")


@merchant_status.handle()
async def _handle_status(matcher: Matcher, event: Event) -> None:
    """查看本群远行商人推送订阅。"""
    group_key = _group_key(event)
    if not group_key:
        await matcher.finish("只能在群聊中查看订阅")
    sub = get_subscription(group_key)
    if not sub:
        await matcher.finish("本群没有远行商人推送订阅")
    keywords = sub.get("keywords") or []
    label = "、".join(str(item) for item in keywords) if keywords else "全部商品刷新"
    await matcher.finish(f"本群远行商人推送：{label}")
