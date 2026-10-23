"""短期会话信息缓存。"""

from __future__ import annotations

from nonebot.adapters import Bot

from .model import User, Scene, Member, Session, SceneType
from ...utils.cache import cache

UNINFO_CACHE = True
UNINFO_CACHE_EXPIRE = 300

def bot_cache_key(bot: Bot) -> str:
    return str(getattr(bot, "self_id", ""))


def session_cache_key(bot: Bot, session_id: str) -> tuple[str, str, str, str]:
    return ("uninfo", "session", bot_cache_key(bot), session_id)


def get_session(bot: Bot, session_id: str) -> Session | None:
    return cache.get(session_cache_key(bot, session_id))


def get_user(bot: Bot, user_id: str) -> User | None:
    return cache.get(("uninfo", "user", bot_cache_key(bot), str(user_id)))


def get_scene(
    bot: Bot,
    scene_type: SceneType,
    scene_id: str,
    *,
    parent_scene_id: str | None = None,
) -> Scene | None:
    key = (
        "uninfo",
        "scene",
        bot_cache_key(bot),
        scene_type.value,
        str(scene_id),
        str(parent_scene_id) if parent_scene_id else "",
    )
    return cache.get(key)


def get_member(bot: Bot, scene_type: SceneType, scene_id: str, user_id: str) -> Member | None:
    return cache.get(("uninfo", "member", bot_cache_key(bot), scene_type.value, str(scene_id), str(user_id)))


def save_user(bot: Bot, user: User) -> None:
    if not UNINFO_CACHE:
        return
    cache.set(("uninfo", "user", bot_cache_key(bot), str(user.id)), user, ttl=UNINFO_CACHE_EXPIRE)


def save_scene(bot: Bot, scene: Scene) -> None:
    if not UNINFO_CACHE:
        return
    parent_scene_id = scene.parent.id if scene.parent else None
    key = (
        "uninfo",
        "scene",
        bot_cache_key(bot),
        scene.type.value,
        str(scene.id),
        str(parent_scene_id) if parent_scene_id else "",
    )
    cache.set(key, scene, ttl=UNINFO_CACHE_EXPIRE)


def save_member(bot: Bot, scene_type: SceneType, scene_id: str, member: Member) -> None:
    if not UNINFO_CACHE:
        return
    key = ("uninfo", "member", bot_cache_key(bot), scene_type.value, str(scene_id), str(member.id))
    cache.set(key, member, ttl=UNINFO_CACHE_EXPIRE)
    save_user(bot, member.user)


def save_session(bot: Bot, session_id: str, session: Session) -> None:
    if not UNINFO_CACHE:
        return
    cache.set(session_cache_key(bot, session_id), session, ttl=UNINFO_CACHE_EXPIRE)
    save_user(bot, session.user)
    save_scene(bot, session.scene)
    if session.member:
        member_scene_id = session.scene.parent.id if session.scene.parent else session.scene.id
        save_member(bot, session.scene.type, member_scene_id, session.member)
