"""统一会话信息模型。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from enum import Enum, IntEnum
from typing import Any


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
