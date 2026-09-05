"""从 NoneBot 事件提取统一会话信息。"""

from __future__ import annotations

from typing import Any

from nonebot.adapters import Bot, Event

from ..core.bot import PlatformBot
from ..core.events import event_group_id, event_user_id, event_user_name
from ..core.registry import adapter_name
from .model import Member, Role, Scene, SceneType, Session, SupportScope, User


def role_from_text(role: Any) -> Role:
    text = str(role or "member")
    if text == "owner":
        return Role("OWNER", 100, "owner")
    if text == "admin":
        return Role("ADMINISTRATOR", 10, "admin")
    return Role("MEMBER", 1, "member")


def scope_for_adapter(adapter: str) -> str:
    return SupportScope.unknown.value


def get_field(source: Any, *names: str) -> Any:
    for name in names:
        value = source.get(name) if isinstance(source, dict) else getattr(source, name, None)
        if value not in (None, ""):
            return value
    return None


async def build_session(bot: Bot, event: Event) -> Session:
    adapter = adapter_name(bot)
    user_id = event_user_id(event)
    group_id = event_group_id(event)
    sender = getattr(event, "sender", None)
    author = getattr(event, "author", None)
    name = event_user_name(event, user_id)
    nick = event_user_name(event, name)
    client = PlatformBot(bot)
    avatar = client.user_avatar(user_id)
    user = User(
        id=user_id,
        name=name,
        nick=nick,
        avatar=avatar,
        gender=str(get_field(sender, "sex", "gender") or "unknown"),
    )

    if group_id:
        group_name = get_field(event, "group_name", "guild_name")
        scene = Scene(
            id=group_id,
            type=SceneType.GROUP,
            name=str(group_name) if group_name else None,
            avatar=client.group_avatar(group_id),
        )
        role = role_from_text(
            get_field(sender, "role", "member_role") or get_field(author, "member_role")
        )
        member = Member(user=user, nick=nick, roles=[role])
    else:
        scene = Scene(id=user_id, type=SceneType.PRIVATE, name=name, avatar=avatar)
        member = None

    return Session(
        self_id=str(getattr(bot, "self_id", "")),
        adapter=adapter,
        scope=scope_for_adapter(adapter),
        scene=scene,
        user=user,
        member=member,
    )
