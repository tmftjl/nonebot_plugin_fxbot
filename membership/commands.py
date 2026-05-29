"""续费、查到期和控制台登录命令。"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from nonebot import get_bots, get_driver
from nonebot.adapters import Bot, Event
from nonebot.matcher import Matcher
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from ..config import get_manager as get_config_manager
from ..console.auth import rotate_console_token
from ..db import with_session
from ..permission import PermLevel, PermScene
from ..plugin import Plugin

from .guard import membership_guard
from .models import MembershipGroup
from .service import MembershipError, membership_service

P = Plugin("membership", category="system", display_name="会员系统")

_UNIT_TO_SERVICE = {"天": "day", "月": "month", "年": "year"}
_SERVICE_TO_UNIT = {"day": "天", "days": "天", "d": "天", "month": "月", "months": "月", "m": "月", "year": "年", "years": "年", "y": "年"}


class _MembershipCommandStore:
    """会员命令数据库操作。"""

    @with_session
    async def adjust_managed_bots(self, session: AsyncSession, groups_by_bot: dict[str, list[Any]]) -> tuple[int, int]:
        result = await session.execute(select(MembershipGroup))
        rows = {row.group_id: row for row in result.scalars().all()}
        updated_count = 0
        unchanged_count = 0

        for bot_id, groups in groups_by_bot.items():
            for item in groups:
                group_id = _normalize_id(
                    item.get("group_id") if isinstance(item, dict) else getattr(item, "group_id", None)
                )
                if not group_id or group_id not in rows:
                    continue
                row = rows[group_id]
                if row.managed_by_bot == str(bot_id):
                    unchanged_count += 1
                    continue
                row.managed_by_bot = str(bot_id)
                updated_count += 1

        return updated_count, unchanged_count


_command_store = _MembershipCommandStore()

console_login_cmd = P.on_regex(
    r"^今汐登录$",
    name="console_login",
    display_name="控制台登录",
    priority=5,
    block=True,
    level=PermLevel.SUPERUSER,
    scene=PermScene.PRIVATE,
    log=True,
)

gen_code_cmd = P.on_regex(
    r"^ww生成续费码(\d+)(天|月|年)$",
    name="membership_generate_code",
    display_name="生成续费码",
    priority=5,
    block=True,
    level=PermLevel.SUPERUSER,
    scene=PermScene.PRIVATE,
    log=True,
)

renew_cmd = P.on_regex(
    r"^ww续费(\d+)(天|月|年)-([A-Za-z0-9_]+)$",
    name="membership_renew",
    display_name="续费",
    priority=5,
    block=True,
    log=True,
)

expiry_cmd = P.on_regex(
    r"^ww到期$",
    name="membership_expiry",
    display_name="查到期",
    priority=5,
    block=True,
    log=True,
)

prompt_cmd = P.on_regex(
    r"^ww(拉群|续费)$",
    name="membership_prompt",
    display_name="续费提示",
    priority=5,
    block=True,
    log=True,
)

adjust_bot_cmd = P.on_regex(
    r"^ww续费调整$",
    name="membership_adjust_bot",
    display_name="续费调整",
    priority=5,
    block=True,
    level=PermLevel.SUPERUSER,
    scene=PermScene.PRIVATE,
    log=True,
)

manual_check_cmd = P.on_regex(
    r"^ww检查会员$",
    name="membership_manual_check",
    display_name="检查会员",
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


def _as_utc(value: datetime | None) -> datetime | None:
    """将 datetime 转为 UTC aware 对象。"""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _format_cn(value: datetime | None) -> str:
    """格式化旧版会员命令时间。"""
    if value is None:
        return "永久"
    return _as_utc(value).astimezone().strftime("%Y-%m-%d %H:%M:%S")  # type: ignore[union-attr]


def _days_remaining(value: datetime) -> int:
    """按自然日计算剩余天数。"""
    expires_at = _as_utc(value)
    if expires_at is None:
        return 999999
    return (expires_at.date() - datetime.now(timezone.utc).date()).days


def _membership_cfg() -> dict[str, Any]:
    """读取会员配置。"""
    cfg = get_config_manager().get_system()
    return cfg["membership"]


def _console_url(token: str) -> str:
    """生成控制台登录地址。"""
    cfg = get_config_manager().get_system()
    console_cfg = cfg["console"]
    path = str(console_cfg["mount_path"])
    host = str(get_driver().config.host)
    port = int(get_driver().config.port)
    return f"http://{host}:{port}{path}?token={token}"


def _code_unit(unit: str) -> str:
    """转换中文续费单位。"""
    return _UNIT_TO_SERVICE[unit]


def _row_unit_cn(unit: str) -> str:
    """转换续费码单位为中文。"""
    return _SERVICE_TO_UNIT.get(str(unit).lower(), str(unit))


def _is_private(event: Any) -> bool:
    """判断是否为私聊事件。"""
    return _gid(event) is None


async def _find_code(code: str):
    """按续费码查找当前记录。"""
    rows = await membership_service.list_codes()
    target = str(code).strip()
    for row in rows:
        if row.code == target:
            return row
    return None


@console_login_cmd.handle()
async def _handle_console_login(matcher: Matcher, event: Event) -> None:
    """处理控制台登录命令。"""
    if not _is_private(event):
        await matcher.finish("请在私聊使用该命令")
    token = rotate_console_token()
    await matcher.finish(f"控制台登录地址：{_console_url(token)}")


@gen_code_cmd.handle()
async def _handle_generate_code(matcher: Matcher, event: Event) -> None:
    """处理生成续费码命令。"""
    if not _is_private(event):
        await matcher.finish("为安全起见，请在私聊生成续费码")
    matched = _plain_text(event)
    match = re.match(r"^ww生成续费码(\d+)(天|月|年)$", matched)
    if not match:
        await matcher.finish("格式错误")
    length = int(match.group(1))
    unit = match.group(2)
    try:
        row = await membership_service.generate_code(
            duration_value=length,
            duration_unit=_code_unit(unit),
            max_use=1,
        )
    except MembershipError as exc:
        await matcher.finish(str(exc))
    public_code = f"ww续费{length}{unit}-{row.code}"
    await matcher.finish(
        f"已生成续费码（默认一次性）：{public_code}\n"
        "请将其发送到需要开通/续费的群聊中（首次开通也使用此码）"
    )


@renew_cmd.handle()
async def _handle_renew(matcher: Matcher, bot: Bot, event: Event) -> None:
    """处理群续费命令。"""
    group_id = _gid(event)
    if not group_id:
        await matcher.finish("续费码只能在群聊中使用哦")

    matched = _plain_text(event)
    match = re.match(r"^ww续费(\d+)(天|月|年)-([A-Za-z0-9_]+)$", matched)
    if not match:
        await matcher.finish("格式错误")
    length = int(match.group(1))
    unit = match.group(2)
    code = match.group(3)

    row = await _find_code(code)
    if row is None or row.status != "active" or row.used_count >= row.max_use:
        await matcher.finish("该续费码无效或已被使用")
    if row.duration_value != length or _row_unit_cn(row.duration_unit) != unit:
        await matcher.finish("续费码信息不匹配，请检查")

    try:
        result = await membership_service.redeem_code(
            code,
            group_id,
            operator_user_id=_uid(event),
            managed_by_bot=str(bot.self_id),
        )
        await membership_guard.invalidate(group_id)
    except MembershipError:
        await matcher.finish("该续费码无效或已被使用")

    await matcher.finish(f"本群会员已成功续费{length}{unit}，到期时间：{_format_cn(result.after_expires_at)}")


@expiry_cmd.handle()
async def _handle_expiry(matcher: Matcher, event: Event) -> None:
    """处理到期查询命令。"""
    group_id = _gid(event)
    if not group_id:
        await matcher.finish("该指令需在群聊中使用")

    try:
        group = await membership_service.get_group(group_id)
    except Exception:
        await matcher.finish("记录损坏，无法解析到期时间")

    if group is None:
        await matcher.finish("未找到本群的会员记录")

    expires_at = _as_utc(group.expires_at)
    if expires_at is None:
        status = "有效"
    else:
        days = _days_remaining(expires_at)
        if days < 0 or group.status != "active":
            status = "已到期"
        elif days == 0:
            status = "今天到期"
        else:
            status = f"有效(剩余{days}天)"

    contact_info = str(_membership_cfg()["contact_info"] or "")
    reply = f"本群会员状态：{status}\n到期时间：{_format_cn(group.expires_at)}"
    if contact_info:
        reply += f"\n{contact_info}"
    await matcher.finish(reply)


@prompt_cmd.handle()
async def _handle_prompt(matcher: Matcher) -> None:
    """处理续费提示命令。"""
    await matcher.finish("如需首次开通或续费,请联系管理员购买续费码（会员开通码），在群内直接发送即可生效")


@adjust_bot_cmd.handle()
async def _handle_adjust_bot(matcher: Matcher, event: Event) -> None:
    """处理续费调整命令。"""
    if not _is_private(event):
        await matcher.finish("续费调整命令请在私聊使用")

    bots = get_bots()
    if not bots:
        await matcher.finish("当前无在线机器人，无法执行续费调整")

    failed_bots: list[str] = []
    groups_by_bot: dict[str, list[Any]] = {}

    for bot_id, bot in bots.items():
        try:
            groups_by_bot[str(bot_id)] = list(await bot.get_group_list())  # type: ignore[attr-defined]
        except Exception:
            failed_bots.append(str(bot_id))
            continue

    updated_count, unchanged_count = await _command_store.adjust_managed_bots(groups_by_bot)

    await membership_guard.reload_all_cache()
    result_msg = f"续费调整完成\n更新: {updated_count} 个群\n未变: {unchanged_count} 个群"
    if failed_bots:
        result_msg += f"\n失败bot: {', '.join(failed_bots)}"
    await matcher.finish(result_msg)


@manual_check_cmd.handle()
async def _handle_manual_check(matcher: Matcher, event: Event) -> None:
    """手动执行会员检查。"""
    if not _is_private(event):
        await matcher.finish("会员检查命令请在私聊使用")
    from .tasks import check_and_process_memberships

    result = await check_and_process_memberships()
    await matcher.finish(f"已处理过期{result.expired}个群，退出{result.left}个群")
