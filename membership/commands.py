"""续费、查到期和控制台登录命令。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from nonebot.adapters import Event
from nonebot.matcher import Matcher

from ..console.auth import rotate_console_token
from ..permission import PermLevel, PermScene
from ..plugin import Plugin

from .guard import membership_guard
from .service import MembershipError, membership_service

P = Plugin("membership", category="system", display_name="会员系统")

renew_cmd = P.on_regex(
    r"^(?:续费|fxbot续费)\s+([A-Za-z0-9_\-]+)$",
    name="membership_renew",
    display_name="续费",
    priority=5,
    block=True,
    log=True,
)

expiry_cmd = P.on_regex(
    r"^(?:查到期|fxbot查到期)$",
    name="membership_expiry",
    display_name="查到期",
    priority=5,
    block=True,
    log=True,
)

console_login_cmd = P.on_regex(
    r"^控制台登录$",
    name="console_login",
    display_name="控制台登录",
    priority=5,
    block=True,
    level=PermLevel.SUPERUSER,
    scene=PermScene.PRIVATE,
    log=True,
)


def _normalize_id(value: Any) -> str | None:
    """标准化 ID。"""
    if value is None:
        return None
    text = str(value).strip()
    return text if text and text != "0" else None


def _gid(event: Any) -> str | None:
    """提取群 ID。"""
    if hasattr(event, "get_group_id"):
        try:
            return _normalize_id(event.get_group_id())
        except Exception:
            pass
    return _normalize_id(getattr(event, "group_id", None))


def _uid(event: Any) -> str | None:
    """提取用户 ID。"""
    if hasattr(event, "get_user_id"):
        try:
            return _normalize_id(event.get_user_id())
        except Exception:
            pass
    return _normalize_id(getattr(event, "user_id", None))


def _plain_text(event: Event) -> str:
    """提取事件纯文本。"""
    if hasattr(event, "get_plaintext"):
        try:
            return str(event.get_plaintext()).strip()
        except Exception:
            pass
    return str(event.get_message()).strip()


def _format_dt(value: datetime | None) -> str:
    """格式化到期时间。"""
    if value is None:
        return "永久"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    local_value = value.astimezone()
    return local_value.strftime("%Y-%m-%d %H:%M:%S %Z")


@renew_cmd.handle()
async def _handle_renew(matcher: Matcher, event: Event) -> None:
    """处理群续费命令。"""
    group_id = _gid(event)
    if not group_id:
        await matcher.finish("续费命令只能在群聊中使用")

    text = _plain_text(event)
    code = text.split(maxsplit=1)[1].strip() if len(text.split(maxsplit=1)) > 1 else ""
    if not code:
        await matcher.finish("请提供续费码")

    try:
        result = await membership_service.redeem_code(
            code,
            group_id,
            operator_user_id=_uid(event),
        )
        await membership_guard.invalidate(group_id)
    except MembershipError as exc:
        await matcher.finish(str(exc))

    await matcher.finish(f"续费成功，到期时间：{_format_dt(result.after_expires_at)}")


@expiry_cmd.handle()
async def _handle_expiry(matcher: Matcher, event: Event) -> None:
    """处理查到期命令。"""
    group_id = _gid(event)
    if not group_id:
        await matcher.finish("查到期命令只能在群聊中使用")

    try:
        group = await membership_service.get_group(group_id)
    except Exception:
        await matcher.finish("查询失败，请稍后再试")

    if group is None:
        await matcher.finish("本群未开通会员")
    await matcher.finish(f"本群会员状态：{group.status}\n到期时间：{_format_dt(group.expires_at)}")


@console_login_cmd.handle()
async def _handle_console_login(matcher: Matcher) -> None:
    """处理控制台登录命令。"""
    token = rotate_console_token()
    await matcher.finish(f"控制台 token：{token}")
