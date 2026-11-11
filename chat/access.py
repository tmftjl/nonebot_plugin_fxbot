"""路由与后台回复共用的目标范围和动态准入策略。"""

from __future__ import annotations

from typing import Any

from nonebot import get_bots

from .hooks import ChatHookContext
from ..config import get_manager
from .session_hooks import direct_reply_mode
from .work_schedule import can_reply
from .behavior.reply_gate import proactive_switch


def id_allowlist(cfg: dict[str, Any], key: str) -> set[str]:
    """读取 ID 白名单；空集合表示不限制，不缓存可热更新配置。"""
    value = cfg[key]
    if not isinstance(value, (list, tuple, set)):
        return set()
    return {str(item).strip() for item in value if item is not None and str(item).strip()}


async def continuation_allowed(context: ChatHookContext, bot: Any) -> bool:
    """按当前连接、配置与会员状态判断，后台请求不继承用户管理员豁免。"""
    if get_bots().get(str(bot.self_id)) is not bot:
        return False
    settings = get_manager().get_system()
    chat = settings["chat"]
    if not can_reply(chat, session_id=context.session_id) or direct_reply_mode(context.session_id):
        return False
    if context.group_id:
        allowlist = id_allowlist(chat, "group_allowlist")
        if allowlist and context.group_id not in allowlist:
            return False
        if await proactive_switch(context.session_id) is False:
            return False
    else:
        allowlist = id_allowlist(chat, "private_allowlist")
        if not chat["private_enabled"] or (allowlist and context.user_id not in allowlist):
            return False
    config = settings["membership"]
    if (
        context.group_id
        and config["enabled"]
        and str(bot.self_id) not in {str(item) for item in config["free_bot_ids"]}
    ):
        from ..membership.guard import membership_guard

        decision = await membership_guard.check_membership_detail(context.group_id, "", bot_id=str(bot.self_id))
        return decision.allowed
    return True
