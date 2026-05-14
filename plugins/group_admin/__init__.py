"""内置群管插件。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from nonebot.adapters import Bot, Event
from nonebot.matcher import Matcher
from nonebot.params import RegexGroup

from ...chat.tools import ToolContext, ToolRuntime, tool
from ...permission import PermLevel, PermScene
from ...plugin import Plugin

P = Plugin("group_admin", display_name="群管", enabled=True, level=PermLevel.ADMIN, scene=PermScene.GROUP)


@dataclass
class ServiceResult:
    """群管操作结果。"""

    success: bool
    message: str


def _gid(event: Any) -> str | None:
    """提取群 ID。"""
    value = getattr(event, "group_id", None)
    if value is None and hasattr(event, "get_group_id"):
        try:
            value = event.get_group_id()
        except Exception:
            value = None
    text = str(value or "").strip()
    return text or None


def _uid(event: Any) -> str:
    """提取用户 ID。"""
    if hasattr(event, "get_user_id"):
        try:
            return str(event.get_user_id())
        except Exception:
            pass
    return str(getattr(event, "user_id", "") or "")


def _plain_text(event: Any) -> str:
    """提取纯文本。"""
    if hasattr(event, "get_plaintext"):
        try:
            return str(event.get_plaintext()).strip()
        except Exception:
            pass
    return str(event.get_message()).strip()


def _extract_target_id(event: Any, fallback: str = "") -> int | None:
    """从消息中提取 @ 目标或 QQ 号。"""
    try:
        for segment in event.get_message():
            if str(segment.type) == "at":
                qq = segment.data.get("qq")
                if qq and str(qq) != "all":
                    return int(qq)
    except Exception:
        pass
    match = re.search(r"\d{5,}", fallback or _plain_text(event))
    return int(match.group(0)) if match else None


async def _guard(
    bot: Bot,
    group_id: str,
    operator_id: str,
    *,
    target_id: int | None = None,
    op_name: str = "操作",
) -> ServiceResult:
    """统一群管权限守卫。"""
    try:
        bot_info = await bot.get_group_member_info(group_id=int(group_id), user_id=int(bot.self_id))
        bot_role = bot_info.get("role", "member")
        if bot_role == "member":
            return ServiceResult(False, f"Bot 无管理权限，无法{op_name}")
    except Exception:
        return ServiceResult(False, "Bot 权限获取失败")

    try:
        operator_info = await bot.get_group_member_info(group_id=int(group_id), user_id=int(operator_id))
        operator_role = operator_info.get("role", "member")
        if operator_role not in {"owner", "admin"}:
            return ServiceResult(False, "权限不足")
    except Exception:
        return ServiceResult(False, "操作者权限获取失败")

    if target_id is not None:
        try:
            target_info = await bot.get_group_member_info(group_id=int(group_id), user_id=target_id)
            target_role = target_info.get("role", "member")
            if target_role == "owner":
                return ServiceResult(False, "无法操作群主")
            if target_role == "admin" and operator_role != "owner":
                return ServiceResult(False, "仅群主可操作管理员")
        except Exception:
            pass
    return ServiceResult(True, "允许操作")


async def _mute_member(
    bot: Bot,
    group_id: str,
    user_id: int,
    duration: int,
    *,
    operator_id: str,
) -> ServiceResult:
    """禁言成员。"""
    guard = await _guard(bot, group_id, operator_id, target_id=user_id, op_name="禁言")
    if not guard.success:
        return guard
    try:
        await bot.set_group_ban(group_id=int(group_id), user_id=user_id, duration=max(0, min(duration, 2592000)))
        return ServiceResult(True, "操作成功")
    except Exception as exc:
        return ServiceResult(False, f"操作失败: {exc}")


async def _kick_member(
    bot: Bot,
    group_id: str,
    user_id: int,
    *,
    operator_id: str,
    reject_add: bool = False,
) -> ServiceResult:
    """踢出成员。"""
    guard = await _guard(bot, group_id, operator_id, target_id=user_id, op_name="踢人")
    if not guard.success:
        return guard
    try:
        await bot.set_group_kick(
            group_id=int(group_id),
            user_id=user_id,
            reject_add_request=reject_add,
        )
        return ServiceResult(True, "已移出群聊")
    except Exception as exc:
        return ServiceResult(False, f"操作失败: {exc}")


async def _set_title(
    bot: Bot,
    group_id: str,
    user_id: int,
    title: str,
    *,
    operator_id: str,
) -> ServiceResult:
    """设置群头衔。"""
    if len(title) > 12:
        return ServiceResult(False, "头衔不能超过 12 个字符")
    guard = await _guard(bot, group_id, operator_id, target_id=user_id, op_name="设置头衔")
    if not guard.success:
        return guard
    try:
        await bot.set_group_special_title(group_id=int(group_id), user_id=user_id, special_title=title)
        return ServiceResult(True, "头衔已更新")
    except Exception as exc:
        return ServiceResult(False, f"操作失败: {exc}")


mute_cmd = P.on_regex(
    r"^#禁言\s*(\d{5,})?\s*(\d+)?",
    name="mute_member",
    display_name="禁言",
    priority=5,
    block=True,
    level=PermLevel.ADMIN,
    scene=PermScene.GROUP,
)


@mute_cmd.handle()
async def _handle_mute(matcher: Matcher, bot: Bot, event: Event, groups: tuple = RegexGroup()) -> None:
    """处理禁言命令。"""
    group_id = _gid(event)
    if not group_id:
        await matcher.finish("请在群聊中使用")
    target_id = _extract_target_id(event, groups[0] if groups else "")
    if target_id is None:
        await matcher.finish("请 @ 目标成员或提供 QQ 号")
    duration = int(groups[1] or 600) if groups and len(groups) > 1 and groups[1] else 600
    result = await _mute_member(bot, group_id, target_id, duration, operator_id=_uid(event))
    await matcher.finish(("✅ " if result.success else "❌ ") + result.message)


unmute_cmd = P.on_regex(
    r"^#解禁\s*(\d{5,})?",
    name="unmute_member",
    display_name="解禁",
    priority=5,
    block=True,
    level=PermLevel.ADMIN,
    scene=PermScene.GROUP,
)


@unmute_cmd.handle()
async def _handle_unmute(matcher: Matcher, bot: Bot, event: Event, groups: tuple = RegexGroup()) -> None:
    """处理解禁命令。"""
    group_id = _gid(event)
    if not group_id:
        await matcher.finish("请在群聊中使用")
    target_id = _extract_target_id(event, groups[0] if groups else "")
    if target_id is None:
        await matcher.finish("请 @ 目标成员或提供 QQ 号")
    result = await _mute_member(bot, group_id, target_id, 0, operator_id=_uid(event))
    await matcher.finish(("✅ " if result.success else "❌ ") + result.message)


kick_cmd = P.on_regex(
    r"^#踢\s*(.+)",
    name="kick_member",
    display_name="踢人",
    priority=5,
    block=True,
    level=PermLevel.ADMIN,
    scene=PermScene.GROUP,
)


@kick_cmd.handle()
async def _handle_kick(matcher: Matcher, bot: Bot, event: Event) -> None:
    """处理踢人命令。"""
    group_id = _gid(event)
    if not group_id:
        await matcher.finish("请在群聊中使用")
    target_id = _extract_target_id(event)
    if target_id is None:
        await matcher.finish("请 @ 目标成员或提供 QQ 号")
    result = await _kick_member(bot, group_id, target_id, operator_id=_uid(event))
    await matcher.finish(("✅ " if result.success else "❌ ") + result.message)


title_cmd = P.on_regex(
    r"^#设置头衔\s*(.+)",
    name="set_title",
    display_name="设置头衔",
    priority=5,
    block=True,
    level=PermLevel.ADMIN,
    scene=PermScene.GROUP,
)


@title_cmd.handle()
async def _handle_title(matcher: Matcher, bot: Bot, event: Event) -> None:
    """处理设置头衔命令。"""
    group_id = _gid(event)
    if not group_id:
        await matcher.finish("请在群聊中使用")
    text = re.sub(r"^#设置头衔\s*", "", _plain_text(event)).strip()
    target_id = _extract_target_id(event, text)
    if target_id is None:
        await matcher.finish("请 @ 目标成员或提供 QQ 号")
    title = re.sub(r"^\d{5,}\s*", "", text).strip()
    if not title:
        await matcher.finish("请提供头衔内容")
    result = await _set_title(bot, group_id, target_id, title, operator_id=_uid(event))
    await matcher.finish(("✅ " if result.success else "❌ ") + result.message)


@tool(
    name="mute_member",
    description="禁言群成员。高风险工具，仅可在群聊上下文中使用。",
    parameters={
        "type": "object",
        "properties": {
            "user_id": {"type": "integer", "description": "目标用户 QQ 号"},
            "duration": {"type": "integer", "description": "禁言秒数"},
        },
        "required": ["user_id", "duration"],
    },
)
async def mute_member_tool(ctx: ToolContext, rt: ToolRuntime, user_id: int, duration: int) -> dict[str, Any]:
    """AI 工具：禁言群成员。"""
    if not ctx.group_id:
        return {"success": False, "message": "只能在群聊中使用"}
    result = await _mute_member(rt.require_bot(), ctx.group_id, user_id, duration, operator_id=ctx.user_id)
    return {"success": result.success, "message": result.message}


@tool(
    name="kick_member",
    description="踢出群成员。高风险工具，仅可在群聊上下文中使用。",
    parameters={
        "type": "object",
        "properties": {"user_id": {"type": "integer", "description": "目标用户 QQ 号"}},
        "required": ["user_id"],
    },
)
async def kick_member_tool(ctx: ToolContext, rt: ToolRuntime, user_id: int) -> dict[str, Any]:
    """AI 工具：踢出群成员。"""
    if not ctx.group_id:
        return {"success": False, "message": "只能在群聊中使用"}
    result = await _kick_member(rt.require_bot(), ctx.group_id, user_id, operator_id=ctx.user_id)
    return {"success": result.success, "message": result.message}
