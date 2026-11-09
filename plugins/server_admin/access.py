"""服务器运维独立的失败封闭权限检查。"""

from __future__ import annotations

from nonebot import logger

from .store import enabled, base_session, server_session, is_server_session
from ...adapter import event_user_id, event_group_id, event_is_group, event_is_private
from ...permission import get_storage, is_superuser
from ...chat.message_adapter import platform_for_bot

GROUP_EVENT_ATTR = "_fxbot_server_group"


def plugin_enabled(command: str = "mode") -> bool:
    """显式要求权限节点存在且开关开启，缺配置不放行。"""
    try:
        config = get_storage().load()
        node = config["sub_plugins"]["server_admin"]
        return (
            config["top"].get("enabled") is True
            and node["top"].get("enabled") is True
            and (node["commands"].get(command) or {}).get("enabled") is True
        )
    except Exception:
        logger.opt(exception=True).debug("[ServerAdmin] 插件开关不可用")
        return False


def _base_for(bot, user_id) -> str:
    """用真实平台、Bot 和用户号构造私聊基础会话。"""
    user = str(user_id or "")
    self_id = str(getattr(bot, "self_id", "") or "")
    if not user or not self_id:
        raise ValueError("身份缺失")
    return f"{platform_for_bot(bot)}:{self_id}:private:{user}"


def actor_base(bot, event) -> str:
    """从真实平台事件取身份，不经过会话解析器，防止伪造工具上下文。"""
    if bot is None or event is None or not event_is_private(event) or event_group_id(event):
        raise ValueError("仅限超级用户私聊")
    return _base_for(bot, event_user_id(event))


def group_actor_base(bot, event) -> str:
    """群聊触发时用发送者的私聊身份派生同一服务器会话。"""
    if bot is None or event is None or not event_is_group(event) or not event_group_id(event):
        raise ValueError("仅限群聊")
    return _base_for(bot, event_user_id(event))


def mark_group_event(event) -> None:
    """标记这条群消息已经过 #服务器模式# 入口，供工具执行时复核。"""
    object.__setattr__(event, GROUP_EVENT_ATTR, True)


def owner_allowed(bot, event) -> bool:
    """真实私聊加显式超级用户和插件开关，任何异常均拒绝。"""
    try:
        actor_base(bot, event)
        return plugin_enabled() and is_superuser(str(event_user_id(event)))
    except Exception:
        logger.opt(exception=True).debug("[ServerAdmin] 超级用户校验未通过")
        return False


def owner_allowed_group(bot, event) -> bool:
    """群聊入口同样要求真实群事件、超级用户和插件开关。"""
    try:
        group_actor_base(bot, event)
        return plugin_enabled("group_mode") and is_superuser(str(event_user_id(event)))
    except Exception:
        logger.opt(exception=True).debug("[ServerAdmin] 群聊超级用户校验未通过")
        return False


def execution_allowed(session_id: str, bot, event, user_id: str = "", group_id=None) -> bool:
    """校验派生会话、实际事件、模式状态及工具身份均一致。"""
    try:
        if not event or not is_server_session(session_id):
            return False
        if user_id and user_id != str(event_user_id(event)):
            return False
        if getattr(event, GROUP_EVENT_ATTR, False) is True:
            # 群聊只认 #服务器模式# 入口标记过、且属于同一超级用户私聊域的会话。
            return (
                not group_id
                and bool(event_group_id(event))
                and plugin_enabled("group_mode")
                and is_superuser(str(event_user_id(event) or ""))
                and server_session(group_actor_base(bot, event)) == session_id
                and enabled(session_id)
            )
        return (
            not group_id
            and owner_allowed(bot, event)
            and base_session(session_id) == actor_base(bot, event)
            and server_session(actor_base(bot, event)) == session_id
            and enabled(session_id)
        )
    except Exception:
        logger.opt(exception=True).debug("[ServerAdmin] 执行边界校验未通过")
        return False
