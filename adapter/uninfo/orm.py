"""统一会话信息持久化。"""

from __future__ import annotations

import asyncio
import json

from nonebot import get_bots
from nonebot.adapters import Bot
from sqlalchemy import JSON, Column, UniqueConstraint, exc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import Field, SQLModel, select

from ...db import with_session
from ..core.registry import adapter_name
from .model import Member, Scene, SceneType, Session, User


class BotModel(SQLModel, table=True):
    """持久化 Bot。"""

    __tablename__ = "nonebot_plugin_uninfo_botmodel"
    __table_args__ = (
        UniqueConstraint("self_id", "adapter", name="nonebot_plugin_uninfo_unique_bot"),
    )

    id: int | None = Field(default=None, primary_key=True)
    self_id: str = Field(max_length=64, nullable=False)
    adapter: str = Field(max_length=32, nullable=False)
    scope: str = Field(max_length=32, nullable=False)

    def get_bot(self) -> Bot | None:
        for bot in list(get_bots().values()):
            if str(bot.self_id) == self.self_id and adapter_name(bot) == self.adapter:
                return bot
        return None


class SceneModel(SQLModel, table=True):
    """持久化场景。"""

    __tablename__ = "nonebot_plugin_uninfo_scenemodel"
    __table_args__ = (
        UniqueConstraint(
            "bot_persist_id",
            "scene_id",
            "scene_type",
            name="nonebot_plugin_uninfo_unique_scene",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    bot_persist_id: int = Field(nullable=False)
    parent_scene_persist_id: int | None = Field(default=None, nullable=True)
    scene_id: str = Field(max_length=64, nullable=False)
    scene_type: int = Field(nullable=False)
    scene_data: dict = Field(sa_column=Column(JSON, nullable=False))

    async def to_scene(self) -> Scene:
        parent_model = (
            await get_scene_model(self.parent_scene_persist_id)
            if self.parent_scene_persist_id
            else None
        )
        return Scene.load(
            {
                **self.scene_data,
                "id": self.scene_id,
                "type": self.scene_type,
                "parent": (
                    {
                        **parent_model.scene_data,
                        "id": parent_model.scene_id,
                        "type": parent_model.scene_type,
                    }
                    if parent_model
                    else None
                ),
            }
        )

    async def query_scene(self) -> Scene | None:
        bot_model = await get_bot_model(self.bot_persist_id)
        bot = bot_model.get_bot()
        if bot is None:
            return None
        from .interface import get_interface

        scene = await self.to_scene()
        return await get_interface(bot).query_scene(
            SceneType(scene.type),
            scene.id,
            parent_scene_id=scene.parent.id if scene.parent else None,
        )


class UserModel(SQLModel, table=True):
    """持久化用户。"""

    __tablename__ = "nonebot_plugin_uninfo_usermodel"
    __table_args__ = (
        UniqueConstraint("bot_persist_id", "user_id", name="nonebot_plugin_uninfo_unique_user"),
    )

    id: int | None = Field(default=None, primary_key=True)
    bot_persist_id: int = Field(nullable=False)
    user_id: str = Field(max_length=64, nullable=False)
    user_data: dict = Field(sa_column=Column(JSON, nullable=False))

    async def to_user(self) -> User:
        return User.load({**self.user_data, "id": self.user_id})

    async def query_user(self) -> User | None:
        bot_model = await get_bot_model(self.bot_persist_id)
        bot = bot_model.get_bot()
        if bot is None:
            return None
        from .interface import get_interface

        return await get_interface(bot).query_user(self.user_id)


class SessionModel(SQLModel, table=True):
    """持久化会话。"""

    __tablename__ = "nonebot_plugin_uninfo_sessionmodel"
    __table_args__ = (
        UniqueConstraint(
            "bot_persist_id",
            "scene_persist_id",
            "user_persist_id",
            name="nonebot_plugin_uninfo_unique_session",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    bot_persist_id: int = Field(nullable=False)
    scene_persist_id: int = Field(nullable=False)
    user_persist_id: int = Field(nullable=False)
    member_data: dict | None = Field(default=None, sa_column=Column(JSON, nullable=True))

    async def to_session(self) -> Session:
        bot_model = await get_bot_model(self.bot_persist_id)
        scene_model = await get_scene_model(self.scene_persist_id)
        user_model = await get_user_model(self.user_persist_id)
        return Session(
            self_id=bot_model.self_id,
            adapter=bot_model.adapter,
            scope=bot_model.scope,
            scene=await scene_model.to_scene(),
            user=await user_model.to_user(),
            member=Member.load(self.member_data) if self.member_data else None,
        )

    async def query_session(self) -> Session | None:
        bot_model = await get_bot_model(self.bot_persist_id)
        bot = bot_model.get_bot()
        if bot is None:
            return None
        from .interface import get_interface

        interface = get_interface(bot)
        scene_model = await get_scene_model(self.scene_persist_id)
        scene = await scene_model.to_scene()
        fresh_scene = await interface.query_scene(
            SceneType(scene.type),
            scene.id,
            parent_scene_id=scene.parent.id if scene.parent else None,
        )
        if fresh_scene is None:
            return None
        user_model = await get_user_model(self.user_persist_id)
        fresh_user = await interface.query_user(user_model.user_id)
        if fresh_user is None:
            return None
        member = await interface.query_member(
            SceneType(scene_model.scene_type), scene_model.scene_id, user_model.user_id
        )
        return Session(
            self_id=bot_model.self_id,
            adapter=bot_model.adapter,
            scope=bot_model.scope,
            scene=fresh_scene,
            user=fresh_user,
            member=member,
        )


_insert_mutex: asyncio.Lock | None = None


def _get_insert_mutex() -> asyncio.Lock:
    global _insert_mutex
    if _insert_mutex is None:
        _insert_mutex = asyncio.Lock()
    return _insert_mutex


class _UninfoStore:
    """会话信息持久化存储。"""

    @with_session
    async def get_bot_persist_id(self, db_session: AsyncSession, basic_info: dict[str, str]) -> int:
        statement = (
            select(BotModel)
            .where(BotModel.self_id == basic_info["self_id"])
            .where(BotModel.adapter == basic_info["adapter"])
        )
        if row := (await db_session.execute(statement)).scalar_one_or_none():
            row.scope = basic_info["scope"]
            await db_session.flush()
            assert row.id is not None
            return row.id

        row = BotModel(
            self_id=basic_info["self_id"],
            adapter=basic_info["adapter"],
            scope=basic_info["scope"],
        )
        async with _get_insert_mutex():
            try:
                db_session.add(row)
                await db_session.flush()
                assert row.id is not None
                return row.id
            except exc.IntegrityError:
                await db_session.rollback()
                persisted_id = (await db_session.execute(statement)).scalar_one().id
                assert persisted_id is not None
                return persisted_id

    @with_session
    async def get_scene_persist_id(
        self, db_session: AsyncSession, basic_info: dict[str, str], scene: Scene
    ) -> int:
        bot_persist_id = await self.get_bot_persist_id(basic_info, session=db_session)
        parent_id = (
            await self.get_scene_persist_id(basic_info, scene.parent, session=db_session)
            if scene.parent
            else None
        )
        scene_data = json.loads(scene.dump_json())
        statement = (
            select(SceneModel)
            .where(SceneModel.bot_persist_id == bot_persist_id)
            .where(SceneModel.scene_id == scene.id)
            .where(SceneModel.scene_type == scene.type.value)
        )
        if row := (await db_session.execute(statement)).scalar_one_or_none():
            row.parent_scene_persist_id = parent_id
            row.scene_data = scene_data
            await db_session.flush()
            assert row.id is not None
            return row.id

        row = SceneModel(
            bot_persist_id=bot_persist_id,
            parent_scene_persist_id=parent_id,
            scene_id=scene.id,
            scene_type=scene.type.value,
            scene_data=scene_data,
        )
        async with _get_insert_mutex():
            try:
                db_session.add(row)
                await db_session.flush()
                assert row.id is not None
                return row.id
            except exc.IntegrityError:
                await db_session.rollback()
                persisted_id = (await db_session.execute(statement)).scalar_one().id
                assert persisted_id is not None
                return persisted_id

    @with_session
    async def get_user_persist_id(
        self, db_session: AsyncSession, basic_info: dict[str, str], user: User
    ) -> int:
        bot_persist_id = await self.get_bot_persist_id(basic_info, session=db_session)
        user_data = json.loads(user.dump_json())
        statement = (
            select(UserModel)
            .where(UserModel.bot_persist_id == bot_persist_id)
            .where(UserModel.user_id == user.id)
        )
        if row := (await db_session.execute(statement)).scalar_one_or_none():
            row.user_data = user_data
            await db_session.flush()
            assert row.id is not None
            return row.id

        row = UserModel(bot_persist_id=bot_persist_id, user_id=user.id, user_data=user_data)
        async with _get_insert_mutex():
            try:
                db_session.add(row)
                await db_session.flush()
                assert row.id is not None
                return row.id
            except exc.IntegrityError:
                await db_session.rollback()
                persisted_id = (await db_session.execute(statement)).scalar_one().id
                assert persisted_id is not None
                return persisted_id

    @with_session
    async def get_session_persist_id(self, db_session: AsyncSession, info_session: Session) -> int:
        bot_persist_id = await self.get_bot_persist_id(info_session.basic, session=db_session)
        scene_persist_id = await self.get_scene_persist_id(
            info_session.basic, info_session.scene, session=db_session
        )
        user_persist_id = await self.get_user_persist_id(
            info_session.basic, info_session.user, session=db_session
        )
        member_data = json.loads(info_session.member.dump_json()) if info_session.member else None
        statement = (
            select(SessionModel)
            .where(SessionModel.bot_persist_id == bot_persist_id)
            .where(SessionModel.scene_persist_id == scene_persist_id)
            .where(SessionModel.user_persist_id == user_persist_id)
        )
        if row := (await db_session.execute(statement)).scalar_one_or_none():
            row.member_data = member_data
            await db_session.flush()
            assert row.id is not None
            return row.id

        row = SessionModel(
            bot_persist_id=bot_persist_id,
            scene_persist_id=scene_persist_id,
            user_persist_id=user_persist_id,
            member_data=member_data,
        )
        async with _get_insert_mutex():
            try:
                db_session.add(row)
                await db_session.flush()
                assert row.id is not None
                return row.id
            except exc.IntegrityError:
                await db_session.rollback()
                persisted_id = (await db_session.execute(statement)).scalar_one().id
                assert persisted_id is not None
                return persisted_id

    @with_session
    async def get_bot_model(self, db_session: AsyncSession, persist_id: int) -> BotModel:
        return (
            await db_session.execute(select(BotModel).where(BotModel.id == persist_id))
        ).scalar_one()

    @with_session
    async def get_scene_model(self, db_session: AsyncSession, persist_id: int) -> SceneModel:
        return (
            await db_session.execute(select(SceneModel).where(SceneModel.id == persist_id))
        ).scalar_one()

    @with_session
    async def get_user_model(self, db_session: AsyncSession, persist_id: int) -> UserModel:
        return (
            await db_session.execute(select(UserModel).where(UserModel.id == persist_id))
        ).scalar_one()

    @with_session
    async def get_session_model(self, db_session: AsyncSession, persist_id: int) -> SessionModel:
        return (
            await db_session.execute(select(SessionModel).where(SessionModel.id == persist_id))
        ).scalar_one()

    @with_session
    async def get_user_model_by_key(
        self, db_session: AsyncSession, bot: Bot, user_id: str
    ) -> UserModel | None:
        bot_model = await self._get_bot_model_for_bot(db_session, bot)
        if bot_model is None:
            return None
        return (
            await db_session.execute(
                select(UserModel)
                .where(UserModel.bot_persist_id == bot_model.id)
                .where(UserModel.user_id == str(user_id))
            )
        ).scalar_one_or_none()

    @with_session
    async def get_scene_model_by_key(
        self, db_session: AsyncSession, bot: Bot, scene_type: SceneType, scene_id: str
    ) -> SceneModel | None:
        bot_model = await self._get_bot_model_for_bot(db_session, bot)
        if bot_model is None:
            return None
        return (
            await db_session.execute(
                select(SceneModel)
                .where(SceneModel.bot_persist_id == bot_model.id)
                .where(SceneModel.scene_type == scene_type.value)
                .where(SceneModel.scene_id == str(scene_id))
            )
        ).scalar_one_or_none()

    @with_session
    async def get_session_model_by_key(
        self,
        db_session: AsyncSession,
        bot: Bot,
        scene_type: SceneType,
        scene_id: str,
        user_id: str,
    ) -> SessionModel | None:
        bot_model = await self._get_bot_model_for_bot(db_session, bot)
        if bot_model is None:
            return None
        statement = (
            select(SessionModel)
            .join(SceneModel, SceneModel.id == SessionModel.scene_persist_id)
            .join(UserModel, UserModel.id == SessionModel.user_persist_id)
            .where(SessionModel.bot_persist_id == bot_model.id)
            .where(SceneModel.scene_type == scene_type.value)
            .where(SceneModel.scene_id == str(scene_id))
            .where(UserModel.user_id == str(user_id))
        )
        return (await db_session.execute(statement)).scalar_one_or_none()

    async def _get_bot_model_for_bot(
        self, db_session: AsyncSession, bot: Bot
    ) -> BotModel | None:
        return (
            await db_session.execute(
                select(BotModel)
                .where(BotModel.self_id == str(getattr(bot, "self_id", "")))
                .where(BotModel.adapter == adapter_name(bot))
            )
        ).scalar_one_or_none()


_store = _UninfoStore()


async def get_bot_persist_id(basic_info: dict[str, str]) -> int:
    return await _store.get_bot_persist_id(basic_info)


async def get_scene_persist_id(basic_info: dict[str, str], scene: Scene) -> int:
    return await _store.get_scene_persist_id(basic_info, scene)


async def get_user_persist_id(basic_info: dict[str, str], user: User) -> int:
    return await _store.get_user_persist_id(basic_info, user)


async def get_session_persist_id(
    info_session: Session, *, db_session: AsyncSession | None = None
) -> int:
    if db_session is not None:
        return await _store.get_session_persist_id(info_session, session=db_session)
    return await _store.get_session_persist_id(info_session)


async def get_bot_model(persist_id: int) -> BotModel:
    return await _store.get_bot_model(persist_id)


async def get_scene_model(persist_id: int) -> SceneModel:
    return await _store.get_scene_model(persist_id)


async def get_user_model(persist_id: int) -> UserModel:
    return await _store.get_user_model(persist_id)


async def get_session_model(persist_id: int) -> SessionModel:
    return await _store.get_session_model(persist_id)


async def get_user_model_by_key(bot: Bot, user_id: str) -> UserModel | None:
    return await _store.get_user_model_by_key(bot, user_id)


async def get_scene_model_by_key(
    bot: Bot, scene_type: SceneType, scene_id: str
) -> SceneModel | None:
    return await _store.get_scene_model_by_key(bot, scene_type, scene_id)


async def get_session_model_by_key(
    bot: Bot,
    scene_type: SceneType,
    scene_id: str,
    user_id: str,
) -> SessionModel | None:
    return await _store.get_session_model_by_key(bot, scene_type, scene_id, user_id)
