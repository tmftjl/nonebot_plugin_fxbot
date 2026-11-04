"""服务器运维独立的失败封闭权限检查。"""

from __future__ import annotations

from nonebot import logger

from .store import enabled, base_session, server_session, is_server_session
from ...adapter import event_user_id, event_group_id, event_is_private
from ...permission import get_storage, is_superuser
from ...chat.message_adapter import platform_for_bot


def plugin_enabled() -> bool:
    """显式要求权限节点存在且开关开启，缺配置不放行。"""
    try:
        config = get_storage().load()
        node = config["sub_plugins"]["server_admin"]
        return (
            config["top"].get("enabled") is True
            and node["top"].get("enabled") is True
            and node["commands"]["mode"].get("enabled") is True
        )
    except Exception:
        logger.opt(exception=True).debug("[ServerAdmin] 插件开关不可用")
        return False


def actor_base(bot, event) -> str:
    """从真实平台事件取身份，不经过会话解析器，防止伪造工具上下文。"""
    if bot is None or event is None or not event_is_private(event) or event_group_id(event):
        raise ValueError("仅限主人私聊")
    user = str(event_user_id(event) or "")
    self_id = str(getattr(bot, "self_id", "") or "")
    if not user or not self_id:
        raise ValueError("身份缺失")
    return f"{platform_for_bot(bot)}:{self_id}:private:{user}"


def owner_allowed(bot, event) -> bool:
    """真实私聊加显式主人和插件开关，任何异常均拒绝。"""
    try:
        actor_base(bot, event)
        return plugin_enabled() and is_superuser(str(event_user_id(event)))
    except Exception:
        logger.opt(exception=True).debug("[ServerAdmin] 主人校验未通过")
        return False


def execution_allowed(session_id: str, bot, event, user_id: str = "", group_id=None) -> bool:
    """校验派生会话、实际事件、模式状态及工具身份均一致。"""
    try:
        return (
            not group_id
            and is_server_session(session_id)
            and owner_allowed(bot, event)
            and base_session(session_id) == actor_base(bot, event)
            and server_session(actor_base(bot, event)) == session_id
            and (not user_id or user_id == str(event_user_id(event)))
            and enabled(session_id)
        )
    except Exception:
        logger.opt(exception=True).debug("[ServerAdmin] 执行边界校验未通过")
        return False
