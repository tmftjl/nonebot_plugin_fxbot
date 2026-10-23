"""AI 对话命令：人格切换和清空历史。"""

from __future__ import annotations

from typing import Any

from nonebot import logger, get_driver, on_message
from nonebot.rule import Rule
from nonebot.matcher import Matcher
from nonebot.adapters import Bot, Event
from nonebot.permission import SUPERUSER, Permission

from ..config import get_manager as get_config_manager
from .service import chat_service
from ..adapter import selfBot, normalize_id, event_user_id, event_group_id, event_plain_text
from .bindings import PERSONA, PROVIDER, get_binding, set_binding
from .personas import DEFAULT_PERSONA, load_personas
from .providers import provider_manager
from .message_adapter import session_id_for_event


def _chat_cfg() -> dict[str, Any]:
    """读取 AI 配置。"""
    return get_config_manager().get_system()["chat"]


_COMMAND_PREFIXES = ("/", "#")


def _strip_prefix(text: str) -> str | None:
    """去掉固定的命令前缀，没有前缀时返回 None。"""
    for prefix in _COMMAND_PREFIXES:
        if text.startswith(prefix):
            return text[len(prefix) :].lstrip()
    return None


def _persona_name(text: str) -> str | None:
    """解析人格命令，返回为空表示查询，返回 None 表示不是人格命令。"""
    stripped = _strip_prefix(text.strip())
    if stripped is None or not stripped.startswith("人格"):
        return None
    return stripped[len("人格") :].strip()


def _provider_name(text: str) -> str | None:
    """解析 Provider 命令，返回为空表示查询，返回 None 表示不是 Provider 命令。"""
    stripped = _strip_prefix(text.strip())
    if stripped is None:
        return None
    parts = stripped.split(maxsplit=1)
    if not parts or parts[0].lower() != "provider":
        return None
    return parts[1].strip() if len(parts) > 1 else ""


def _event_group_role(event: Any) -> str:
    """从事件发送者读取群身份。"""
    for member in (getattr(event, "sender", None), getattr(event, "author", None)):
        if isinstance(member, dict):
            value = member.get("role") or member.get("member_role")
        else:
            value = getattr(member, "role", None) or getattr(member, "member_role", None)
        if value:
            return str(value)
    return ""


def _is_privileged(user_id: str) -> bool:
    """判断是否为超级用户或 Bot 管理员。"""
    try:
        superusers = {str(item) for item in get_driver().config.superusers or []}
    except Exception:
        superusers = set()
    if user_id and user_id in superusers:
        return True
    value = get_config_manager().get_system().get("system", {}).get("bot_admins")
    admins = {str(item) for item in value if item is not None} if isinstance(value, (list, tuple, set)) else set()
    return bool(user_id and user_id in admins)


async def _group_role(group_id: str, user_id: str, event: Any) -> str:
    """读取群身份，事件未提供时回查平台。"""
    role = _event_group_role(event)
    if role or not group_id or not user_id:
        return role
    try:
        return str(await selfBot.get_group_member_role(group_id, user_id) or "")
    except Exception:
        logger.opt(exception=True).debug(f"[Chat] 查询群身份失败: group={group_id} user={user_id}")
        return ""


async def _can_manage(event: Event) -> bool:
    """私聊或特权账号可用；群聊要求群主/管理员。"""
    group_id = normalize_id(event_group_id(event))
    if not group_id:
        return True
    user_id = normalize_id(event_user_id(event)) or ""
    return _is_privileged(user_id) or await _group_role(group_id, user_id, event) in {"owner", "admin"}


async def _persona_rule(bot: Bot, event: Event) -> bool:
    """人格切换命令匹配规则。"""
    return _persona_name(event_plain_text(event)) is not None


async def _provider_rule(bot: Bot, event: Event) -> bool:
    """Provider 切换命令匹配规则。"""
    return _provider_name(event_plain_text(event)) is not None


async def _clear_rule(bot: Bot, event: Event) -> bool:
    """清空对话命令匹配规则。"""
    return _strip_prefix(event_plain_text(event).strip()) == "清空对话"


manage_permission = Permission(_can_manage)

persona_cmd = on_message(
    rule=Rule(_persona_rule),
    permission=manage_permission,
    priority=5,
    block=True,
)
provider_cmd = on_message(
    rule=Rule(_provider_rule),
    permission=SUPERUSER,
    priority=5,
    block=True,
)
clear_cmd = on_message(
    rule=Rule(_clear_rule),
    permission=manage_permission,
    priority=5,
    block=True,
)


@persona_cmd.handle()
async def _handle_persona(bot: Bot, event: Event, matcher: Matcher) -> None:
    """切换当前群或当前私聊用户的人格。"""
    group_id = normalize_id(event_group_id(event))
    session_id = session_id_for_event(bot, event)
    scope = "本群" if group_id else ""

    name = _persona_name(event_plain_text(event)) or ""
    personas = load_personas()
    default_name = str(_chat_cfg()["persona"] or "") or DEFAULT_PERSONA

    if not name:
        bound = get_binding(PERSONA, session_id)
        effective = bound if bound in personas else default_name
        await matcher.finish(f"{scope}当前人格：{effective}\n可用人格：{'、'.join(sorted(personas))}")

    if name in {"重置", "恢复默认"}:
        try:
            set_binding(PERSONA, session_id, None)
        except Exception:
            logger.opt(exception=True).warning(f"[Chat] 清除人格绑定失败: session={session_id}")
            await matcher.finish("人格设置失败，请稍后再试")
        await chat_service.clear_session(session_id)
        await matcher.finish(f"已恢复全局默认人格：{default_name}，对话历史已清空")

    if name not in personas:
        await matcher.finish(f"没有名为「{name}」的人格，可用人格：{'、'.join(sorted(personas))}")

    try:
        set_binding(PERSONA, session_id, name)
    except Exception:
        logger.opt(exception=True).warning(f"[Chat] 保存人格绑定失败: session={session_id}")
        await matcher.finish("人格设置失败，请稍后再试")
    await chat_service.clear_session(session_id)
    await matcher.finish(f"{scope}人格已切换为「{name}」，对话历史已清空")


@provider_cmd.handle()
async def _handle_provider(bot: Bot, event: Event, matcher: Matcher) -> None:
    """切换当前群或当前私聊用户使用的 Provider。"""
    group_id = normalize_id(event_group_id(event))
    session_id = session_id_for_event(bot, event)
    scope = "本群" if group_id else "当前会话"

    name = _provider_name(event_plain_text(event)) or ""
    names = sorted(provider_manager.list_providers_by_type("chat"))
    default_name = str(_chat_cfg()["provider"] or "") or "自动（第一个对话 Provider）"
    choices = "、".join(names) or "（未配置）"

    if not name:
        bound = get_binding(PROVIDER, session_id)
        effective = bound if bound in names else default_name
        await matcher.finish(f"{scope}当前 Provider：{effective}\n可用 Provider：{choices}")

    if name in {"重置", "恢复默认"}:
        try:
            set_binding(PROVIDER, session_id, None)
        except Exception:
            logger.opt(exception=True).warning(f"[Chat] 清除 Provider 绑定失败: session={session_id}")
            await matcher.finish("Provider 设置失败，请稍后再试")
        await matcher.finish(f"已恢复全局默认 Provider：{default_name}")

    if name not in names:
        await matcher.finish(f"没有名为「{name}」的对话 Provider，可用 Provider：{choices}")

    try:
        set_binding(PROVIDER, session_id, name)
    except Exception:
        logger.opt(exception=True).warning(f"[Chat] 保存 Provider 绑定失败: session={session_id}")
        await matcher.finish("Provider 设置失败，请稍后再试")
    await matcher.finish(f"{scope} Provider 已切换为「{name}」")


@clear_cmd.handle()
async def _handle_clear(bot: Bot, event: Event, matcher: Matcher) -> None:
    """清空当前会话的 AI 历史。"""
    await chat_service.clear_session(session_id_for_event(bot, event))
    await matcher.finish("已清空当前对话历史")
