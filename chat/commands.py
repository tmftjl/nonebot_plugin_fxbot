"""AI 人格切换命令。"""

from __future__ import annotations

from typing import Any

from nonebot import logger, get_driver, on_message
from nonebot.rule import Rule
from nonebot.matcher import Matcher
from nonebot.adapters import Bot, Event

from ..config import get_manager as get_config_manager
from ..adapter import selfBot, normalize_id, event_user_id, event_group_id, event_plain_text
from .personas import load_personas, get_bound_persona, set_bound_persona
from .message_adapter import session_id_for_event


def _chat_cfg() -> dict[str, Any]:
    """读取 AI 配置。"""
    return get_config_manager().get_system()["chat"]


def _command_prefixes() -> tuple[str, ...]:
    """读取命令前缀，空配置回退到 /。"""
    value = _chat_cfg()["command_prefixes"]
    prefixes = tuple(str(item) for item in value if str(item).strip()) if isinstance(value, (list, tuple, set)) else ()
    return prefixes or ("/",)


def _strip_prefix(text: str) -> str | None:
    """去掉命令前缀，没有前缀时返回 None。"""
    for prefix in sorted(_command_prefixes(), key=len, reverse=True):
        if text.startswith(prefix):
            return text[len(prefix) :].lstrip()
    return None


def _persona_name(text: str) -> str | None:
    """解析人格命令，返回为空表示查询，返回 None 表示不是人格命令。"""
    stripped = _strip_prefix(text.strip())
    if stripped is None or not stripped.startswith("人格"):
        return None
    return stripped[len("人格") :].strip()


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


async def _persona_rule(bot: Bot, event: Event) -> bool:
    """人格切换命令匹配规则。"""
    return _persona_name(event_plain_text(event)) is not None


persona_cmd = on_message(rule=Rule(_persona_rule), priority=5, block=True)


@persona_cmd.handle()
async def _handle_persona(bot: Bot, event: Event, matcher: Matcher) -> None:
    """切换当前群或当前私聊用户的人格。"""
    group_id = normalize_id(event_group_id(event))
    user_id = normalize_id(event_user_id(event)) or ""
    session_id = session_id_for_event(event)
    scope = "本群" if group_id else ""

    if group_id and not _is_privileged(user_id):
        role = await _group_role(group_id, user_id, event)
        if role not in {"owner", "admin"}:
            await matcher.finish("只有群主或管理员可以切换本群人格")

    name = _persona_name(event_plain_text(event)) or ""
    personas = load_personas()
    default_name = str(_chat_cfg()["persona"] or "default")

    if not name:
        bound = get_bound_persona(session_id)
        effective = bound if bound in personas else default_name
        await matcher.finish(f"{scope}当前人格：{effective}\n可用人格：{'、'.join(sorted(personas))}")

    if name in {"重置", "恢复默认"}:
        try:
            set_bound_persona(session_id, None)
        except Exception:
            logger.opt(exception=True).warning(f"[Chat] 清除人格绑定失败: session={session_id}")
            await matcher.finish("人格设置失败，请稍后再试")
        await matcher.finish(f"已恢复全局默认人格：{default_name}")

    if name not in personas:
        await matcher.finish(f"没有名为「{name}」的人格，可用人格：{'、'.join(sorted(personas))}")

    try:
        set_bound_persona(session_id, name)
    except Exception:
        logger.opt(exception=True).warning(f"[Chat] 保存人格绑定失败: session={session_id}")
        await matcher.finish("人格设置失败，请稍后再试")
    await matcher.finish(f"{scope}人格已切换为「{name}」")
