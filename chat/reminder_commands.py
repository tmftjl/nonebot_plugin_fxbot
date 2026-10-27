"""提醒确认与取消命令。"""

from __future__ import annotations

from nonebot import logger, on_message
from nonebot.rule import Rule
from nonebot.matcher import Matcher
from nonebot.adapters import Bot, Event

from ..adapter import normalize_id, event_user_id, event_plain_text
from .commands import _strip_prefix
from .reminders import cancel, confirm
from .message_adapter import session_id_for_event


def _parse(text: str) -> tuple[str, str] | None:
    """解析「确认提醒/取消提醒 + 编号」。"""
    stripped = _strip_prefix(text.strip())
    if stripped is None:
        return None
    parts = stripped.split(maxsplit=1)
    if not parts or parts[0] not in {"确认提醒", "取消提醒"}:
        return None
    return parts[0], parts[1].strip() if len(parts) > 1 else ""


def _confirm_rule(bot: Bot, event: Event) -> bool:
    parsed = _parse(event_plain_text(event))
    return bool(parsed and parsed[0] == "确认提醒")


def _cancel_rule(bot: Bot, event: Event) -> bool:
    parsed = _parse(event_plain_text(event))
    return bool(parsed and parsed[0] == "取消提醒")


confirm_cmd = on_message(rule=Rule(_confirm_rule), priority=4, block=True)
cancel_cmd = on_message(rule=Rule(_cancel_rule), priority=4, block=True)


def _reminder_id(value: str) -> int | None:
    """读取提醒编号；非法时返回 None。"""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


async def _handle(bot: Bot, event: Event, matcher: Matcher, *, action: str) -> None:
    parsed = _parse(event_plain_text(event))
    reminder_id = _reminder_id(parsed[1]) if parsed else None
    if reminder_id is None:
        await matcher.finish(f"用法：#{action}提醒 <编号>")
    tenant = session_id_for_event(bot, event)
    user_id = normalize_id(event_user_id(event)) or ""
    try:
        ok = await (confirm if action == "确认" else cancel)(tenant, user_id, reminder_id)
    except Exception:
        logger.opt(exception=True).warning("[Chat] 提醒命令执行失败")
        await matcher.finish("提醒功能暂不可用，请稍后再试")
        return
    if not ok:
        await matcher.finish("没有找到可操作的提醒")
    await matcher.finish(f"提醒已{action}")


@confirm_cmd.handle()
async def _handle_confirm(bot: Bot, event: Event, matcher: Matcher) -> None:
    """确认提醒后进入待发送队列。"""
    await _handle(bot, event, matcher, action="确认")


@cancel_cmd.handle()
async def _handle_cancel(bot: Bot, event: Event, matcher: Matcher) -> None:
    """取消尚未发送的提醒。"""
    await _handle(bot, event, matcher, action="取消")
