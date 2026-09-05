"""短期会话信息缓存。"""

from __future__ import annotations

import asyncio
from typing import Any

from nonebot.adapters import Bot

from .model import Member, Scene, SceneType, Session, User

UNINFO_CACHE = True
UNINFO_CACHE_EXPIRE = 300

_session_cache: dict[tuple[str, str], Session] = {}
_user_cache: dict[str, dict[str, User]] = {}
_scene_cache: dict[str, dict[tuple[int, str, str | None], Scene]] = {}
_member_cache: dict[str, dict[tuple[int, str, str], Member]] = {}


def bot_cache_key(bot: Bot) -> str:
    return str(getattr(bot, "self_id", ""))


def cache_set(cache: dict[Any, Any], key: Any, value: Any) -> None:
    if not UNINFO_CACHE:
        return
    cache[key] = value
    asyncio.get_running_loop().call_later(UNINFO_CACHE_EXPIRE, cache.pop, key, None)


def session_cache_key(bot: Bot, session_id: str) -> tuple[str, str]:
    return (bot_cache_key(bot), session_id)


def get_session(bot: Bot, session_id: str) -> Session | None:
    return _session_cache.get(session_cache_key(bot, session_id))


def get_user(bot: Bot, user_id: str) -> User | None:
    return _user_cache.get(bot_cache_key(bot), {}).get(str(user_id))


def get_scene(
    bot: Bot,
    scene_type: SceneType,
    scene_id: str,
    *,
    parent_scene_id: str | None = None,
) -> Scene | None:
    key = (scene_type.value, str(scene_id), str(parent_scene_id) if parent_scene_id else None)
    return _scene_cache.get(bot_cache_key(bot), {}).get(key)


def get_member(bot: Bot, scene_type: SceneType, scene_id: str, user_id: str) -> Member | None:
    return _member_cache.get(bot_cache_key(bot), {}).get(
        (scene_type.value, str(scene_id), str(user_id))
    )


def save_user(bot: Bot, user: User) -> None:
    cache_set(_user_cache.setdefault(bot_cache_key(bot), {}), user.id, user)


def save_scene(bot: Bot, scene: Scene) -> None:
    key = (scene.type.value, scene.id, scene.parent.id if scene.parent else None)
    cache_set(_scene_cache.setdefault(bot_cache_key(bot), {}), key, scene)


def save_member(bot: Bot, scene_type: SceneType, scene_id: str, member: Member) -> None:
    key = (scene_type.value, str(scene_id), member.id)
    cache_set(_member_cache.setdefault(bot_cache_key(bot), {}), key, member)
    save_user(bot, member.user)


def save_session(bot: Bot, session_id: str, session: Session) -> None:
    cache_set(_session_cache, session_cache_key(bot, session_id), session)
    save_user(bot, session.user)
    save_scene(bot, session.scene)
    if session.member:
        member_scene_id = session.scene.parent.id if session.scene.parent else session.scene.id
        save_member(bot, session.scene.type, member_scene_id, session.member)
