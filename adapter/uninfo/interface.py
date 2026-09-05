"""主动查询统一会话信息接口。"""

from __future__ import annotations

from nonebot.adapters import Bot

from ..core.bot import PlatformBot
from . import cache
from .fetch import role_from_text
from .model import Member, Role, Scene, SceneType, User


class Interface:
    """按统一模型查询用户、场景、成员信息。"""

    def __init__(self, bot: Bot):
        self.bot = bot
        self.client = PlatformBot(bot)

    async def get_user(self, user_id: str) -> User | None:
        user_id = str(user_id)
        if user := cache.get_user(self.bot, user_id):
            return user
        if user := await self._stored_user(user_id):
            cache.save_user(self.bot, user)
            return user
        user = await self.query_user(user_id)
        if user:
            cache.save_user(self.bot, user)
        return user

    async def query_user(self, user_id: str) -> User | None:
        try:
            info = await self.client.get_user(user_id)
        except Exception:
            return User(
                id=str(user_id),
                name=str(user_id),
                nick=str(user_id),
                avatar=self.client.user_avatar(user_id),
            )
        name = str(info.get("nickname") or info.get("username") or user_id)
        return User(
            id=str(info.get("user_id") or info.get("id") or user_id),
            name=name,
            nick=name,
            avatar=self.client.user_avatar(user_id),
            gender=str(info.get("sex") or "unknown"),
        )

    async def get_scene(
        self,
        scene_type: SceneType,
        scene_id: str,
        *,
        parent_scene_id: str | None = None,
    ) -> Scene | None:
        scene_id = str(scene_id)
        if scene := cache.get_scene(
            self.bot, scene_type, scene_id, parent_scene_id=parent_scene_id
        ):
            return scene
        if scene := await self._stored_scene(scene_type, scene_id):
            cache.save_scene(self.bot, scene)
            return scene
        scene = await self.query_scene(scene_type, scene_id, parent_scene_id=parent_scene_id)
        if scene:
            cache.save_scene(self.bot, scene)
        return scene

    async def query_scene(
        self,
        scene_type: SceneType,
        scene_id: str,
        *,
        parent_scene_id: str | None = None,
    ) -> Scene | None:
        if scene_type == SceneType.PRIVATE:
            user = await self.get_user(scene_id)
            if user:
                return Scene(id=user.id, type=SceneType.PRIVATE, name=user.name, avatar=user.avatar)
            return None
        if scene_type == SceneType.GROUP:
            try:
                info = await self.client.get_group_info(scene_id)
            except Exception:
                return Scene(
                    id=str(scene_id),
                    type=SceneType.GROUP,
                    avatar=self.client.group_avatar(scene_id),
                )
            return Scene(
                id=str(info.get("group_id") or info.get("id") or scene_id),
                type=SceneType.GROUP,
                name=str(info.get("group_name") or info.get("name") or "") or None,
                avatar=self.client.group_avatar(scene_id),
            )
        return None

    async def get_member(
        self, scene_type: SceneType, scene_id: str, user_id: str
    ) -> Member | None:
        scene_id = str(scene_id)
        user_id = str(user_id)
        if member := cache.get_member(self.bot, scene_type, scene_id, user_id):
            return member
        if member := await self._stored_member(scene_type, scene_id, user_id):
            cache.save_member(self.bot, scene_type, scene_id, member)
            return member
        member = await self.query_member(scene_type, scene_id, user_id)
        if member:
            cache.save_member(self.bot, scene_type, scene_id, member)
        return member

    async def query_member(
        self, scene_type: SceneType, scene_id: str, user_id: str
    ) -> Member | None:
        if scene_type != SceneType.GROUP:
            return None
        try:
            info = await self.client.get_group_member(scene_id, user_id)
        except Exception:
            user = await self.get_user(user_id)
            return Member(user=user or User(id=user_id), roles=[Role("MEMBER", 1, "member")])
        name = str(info.get("nickname") or info.get("username") or info.get("user_id") or user_id)
        user = User(
            id=str(info.get("user_id") or info.get("id") or user_id),
            name=name,
            nick=name,
            avatar=self.client.user_avatar(user_id),
            gender=str(info.get("sex") or "unknown"),
        )
        return Member(
            user=user,
            nick=str(info.get("card") or info.get("nickname") or name),
            roles=[role_from_text(info.get("role") or info.get("member_role"))],
        )

    async def _stored_user(self, user_id: str) -> User | None:
        try:
            from .orm import get_user_model_by_key

            model = await get_user_model_by_key(self.bot, user_id)
            return await model.to_user() if model else None
        except Exception:
            return None

    async def _stored_scene(self, scene_type: SceneType, scene_id: str) -> Scene | None:
        try:
            from .orm import get_scene_model_by_key

            model = await get_scene_model_by_key(self.bot, scene_type, scene_id)
            return await model.to_scene() if model else None
        except Exception:
            return None

    async def _stored_member(
        self, scene_type: SceneType, scene_id: str, user_id: str
    ) -> Member | None:
        try:
            from .orm import get_session_model_by_key

            model = await get_session_model_by_key(self.bot, scene_type, scene_id, user_id)
            return Member.load(model.member_data) if model and model.member_data else None
        except Exception:
            return None


def get_interface(bot: Bot) -> Interface:
    """NoneBot 依赖注入入口：返回查询接口。"""
    return Interface(bot)
