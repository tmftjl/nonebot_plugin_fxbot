"""本项目内置的会话信息兼容层。"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from enum import Enum, IntEnum
from typing import Annotated, Any

from nonebot import get_bots
from nonebot.adapters import Bot, Event
from nonebot.params import Depends
from sqlalchemy import JSON, Column, UniqueConstraint, exc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import Field, SQLModel, select

from ..db import with_session
from .bot import PlatformBot
from .events import event_group_id, event_user_id, event_user_name
from .registry import adapter_name


class SupportScope(str, Enum):
    """平台范围。"""

    qq_client = "QQClient"
    qq_api = "QQAPI"
    unknown = "Unknown"


class SceneType(IntEnum):
    """会话场景类型。"""

    PRIVATE = 0
    GROUP = 1
    GUILD = 2
    CHANNEL_TEXT = 3
    CHANNEL_CATEGORY = 4
    CHANNEL_VOICE = 5


@dataclass
class Scene:
    """对话场景。"""

    id: str
    type: SceneType
    name: str | None = None
    avatar: str | None = None
    parent: "Scene | None" = None

    @property
    def is_private(self) -> bool:
        return self.type == SceneType.PRIVATE

    @property
    def is_group(self) -> bool:
        return self.type == SceneType.GROUP

    @property
    def is_guild(self) -> bool:
        return self.type == SceneType.GUILD

    @property
    def is_channel(self) -> bool:
        return self.type.value >= SceneType.CHANNEL_TEXT.value

    def dump_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, default=_json_default)

    @classmethod
    def load(cls, data: dict[str, Any]) -> "Scene":
        parent = cls.load(data["parent"]) if data.get("parent") else None
        return cls(
            id=str(data["id"]),
            type=SceneType(data["type"]),
            name=data.get("name"),
            avatar=data.get("avatar"),
            parent=parent,
        )


@dataclass
class User:
    """用户信息。"""

    id: str
    name: str | None = None
    nick: str | None = None
    avatar: str | None = None
    gender: str = "unknown"

    def dump_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, default=_json_default)

    @classmethod
    def load(cls, data: dict[str, Any]) -> "User":
        return cls(
            id=str(data["id"]),
            name=data.get("name"),
            nick=data.get("nick"),
            avatar=data.get("avatar"),
            gender=str(data.get("gender") or "unknown"),
        )


@dataclass
class Role:
    """成员角色。"""

    id: str
    level: int = 0
    name: str | None = None

    @classmethod
    def load(cls, data: dict[str, Any]) -> "Role":
        return cls(id=str(data["id"]), level=int(data.get("level") or 0), name=data.get("name"))


@dataclass
class MuteInfo:
    """禁言信息。"""

    muted: bool
    duration: timedelta
    start_at: datetime | None = None

    @classmethod
    def load(cls, data: dict[str, Any]) -> "MuteInfo":
        duration = data.get("duration")
        if not isinstance(duration, timedelta):
            duration = timedelta(seconds=float(duration or 0))
        start_at = data.get("start_at")
        if start_at and not isinstance(start_at, datetime):
            start_at = datetime.fromisoformat(str(start_at))
        return cls(bool(data.get("muted")), duration, start_at)


@dataclass
class Member:
    """成员信息。"""

    user: User
    nick: str | None = None
    mute: MuteInfo | None = None
    joined_at: datetime | None = None
    roles: list[Role] = field(default_factory=list)

    @property
    def id(self) -> str:
        return self.user.id

    @property
    def role(self) -> Role | None:
        return max(self.roles, key=lambda role: role.level) if self.roles else None

    def dump_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, default=_json_default)

    @classmethod
    def load(cls, data: dict[str, Any]) -> "Member":
        return cls(
            user=User.load(data["user"]),
            nick=data.get("nick"),
            mute=MuteInfo.load(data["mute"]) if data.get("mute") else None,
            joined_at=_load_datetime(data.get("joined_at")),
            roles=[Role.load(role) for role in data.get("roles") or []],
        )


@dataclass
class Session:
    """会话信息。"""

    self_id: str
    adapter: str
    scope: str
    scene: Scene
    user: User
    member: Member | None = None

    @property
    def id(self) -> str:
        if self.scene.is_private:
            return self.scene_path
        return f"{self.scene_path}_{self.user.id}"

    @property
    def scene_path(self) -> str:
        if self.scene.is_private:
            return f"{self.scene.parent.id}_{self.user.id}" if self.scene.parent else self.user.id
        if self.scene.is_group:
            return self.scene.id
        return f"{self.scene.parent.id}_{self.scene.id}" if self.scene.parent else self.scene.id

    @property
    def basic(self) -> dict[str, str]:
        scope = self.scope.value if isinstance(self.scope, SupportScope) else str(self.scope)
        return {"self_id": self.self_id, "adapter": self.adapter, "scope": scope}


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, timedelta):
        return value.total_seconds()
    if isinstance(value, IntEnum):
        return int(value)
    return value


def _load_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except Exception:
        return None


def _role_from_text(role: Any) -> Role:
    text = str(role or "member")
    if text == "owner":
        return Role("OWNER", 100, "owner")
    if text == "admin":
        return Role("ADMINISTRATOR", 10, "admin")
    return Role("MEMBER", 1, "member")


def _scope_for_adapter(adapter: str) -> str:
    return SupportScope.unknown.value


async def _build_session(bot: Bot, event: Event) -> Session:
    adapter = adapter_name(bot)
    user_id = event_user_id(event)
    group_id = event_group_id(event)
    sender = getattr(event, "sender", None)
    author = getattr(event, "author", None)
    name = str(
        getattr(sender, "nickname", None)
        or getattr(author, "username", None)
        or getattr(author, "member_openid", None)
        or user_id
    )
    nick = event_user_name(event, name)
    client = PlatformBot(bot)
    avatar = client.user_avatar(user_id)
    user = User(
        id=user_id,
        name=name,
        nick=nick,
        avatar=avatar,
        gender=str(getattr(sender, "sex", "unknown") or "unknown"),
    )

    if group_id:
        group_name = None
        try:
            group = await client.get_group_info(group_id)
            group_name = str(group.get("group_name") or group.get("name") or "")
        except Exception:
            group_name = None
        scene = Scene(
            id=group_id,
            type=SceneType.GROUP,
            name=group_name,
            avatar=client.group_avatar(group_id),
        )
        role = _role_from_text(getattr(sender, "role", None))
        join_time = None
        card = nick
        try:
            member_info = await client.get_group_member(group_id, user_id)
            card = str(member_info.get("card") or member_info.get("nickname") or nick)
            role = _role_from_text(member_info.get("role"))
            join_raw = member_info.get("join_time")
            join_time = datetime.fromtimestamp(join_raw) if join_raw else None
        except Exception:
            pass
        member = Member(user=user, nick=card, joined_at=join_time, roles=[role])
    else:
        scene = Scene(id=user_id, type=SceneType.PRIVATE, name=name, avatar=avatar)
        member = None

    return Session(
        self_id=str(getattr(bot, "self_id", "")),
        adapter=adapter,
        scope=_scope_for_adapter(adapter),
        scene=scene,
        user=user,
        member=member,
    )


async def get_session(bot: Bot, event: Event) -> Session:
    """NoneBot 依赖注入入口：返回当前会话信息。"""
    return await _build_session(bot, event)


def UniSession() -> Session:
    return Depends(get_session)


Uninfo = Annotated[Session, UniSession()]


class Interface:
    """按用户 ID 查询基础用户信息。"""

    def __init__(self, bot: Bot):
        self.bot = bot

    async def get_user(self, user_id: str) -> User | None:
        client = PlatformBot(self.bot)
        try:
            info = await client.get_user(user_id)
            name = str(info.get("nickname") or info.get("username") or user_id)
            return User(
                id=str(info.get("user_id") or info.get("id") or user_id),
                name=name,
                nick=name,
                avatar=client.user_avatar(user_id),
                gender=str(info.get("sex") or "unknown"),
            )
        except Exception:
            return User(
                id=str(user_id),
                name=str(user_id),
                nick=str(user_id),
                avatar=client.user_avatar(user_id),
            )


def get_interface(bot: Bot) -> Interface:
    """NoneBot 依赖注入入口：返回查询接口。"""
    return Interface(bot)


def QueryInterface() -> Interface:
    return Depends(get_interface)


QryItrface = Annotated[Interface, QueryInterface()]


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
