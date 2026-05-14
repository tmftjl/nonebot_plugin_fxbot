"""权限枚举和上下文类型。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, IntEnum
from typing import Any


class PermLevel(IntEnum):
    """权限等级。"""

    LOW = 0
    MEMBER = 1
    ADMIN = 2
    OWNER = 3
    BOT_ADMIN = 4
    SUPERUSER = 5

    @staticmethod
    def from_str(value: str | None) -> "PermLevel":
        """从字符串转换权限等级。"""
        key = str(value or "member").strip().lower()
        mapping = {
            "all": PermLevel.LOW,
            "low": PermLevel.LOW,
            "member": PermLevel.MEMBER,
            "admin": PermLevel.ADMIN,
            "owner": PermLevel.OWNER,
            "bot_admin": PermLevel.BOT_ADMIN,
            "superuser": PermLevel.SUPERUSER,
        }
        return mapping.get(key, PermLevel.MEMBER)


class PermScene(Enum):
    """权限适用场景。"""

    ALL = "all"
    GROUP = "group"
    PRIVATE = "private"

    @staticmethod
    def from_str(value: str | None) -> "PermScene":
        """从字符串转换权限场景。"""
        key = str(value or "all").strip().lower()
        mapping = {
            "group": PermScene.GROUP,
            "private": PermScene.PRIVATE,
        }
        return mapping.get(key, PermScene.ALL)


class Decision(Enum):
    """策略决策。"""

    ALLOW = "allow"
    DENY = "deny"
    SKIP = "skip"


@dataclass
class PermContext:
    """权限判定上下文。"""

    user_id: str
    group_id: str | None
    user_level: PermLevel
    is_group: bool
    is_private: bool


@dataclass
class PolicyResult:
    """权限策略评估结果。"""

    decision: Decision
    reason: str = ""

    @classmethod
    def allow(cls, reason: str = "") -> "PolicyResult":
        """允许通过。"""
        return cls(decision=Decision.ALLOW, reason=reason)

    @classmethod
    def deny(cls, reason: str) -> "PolicyResult":
        """拒绝通过。"""
        return cls(decision=Decision.DENY, reason=reason)

    @classmethod
    def skip(cls) -> "PolicyResult":
        """跳过当前策略。"""
        return cls(decision=Decision.SKIP)


def perm_entry_default(level: str = "member", scene: str = "all") -> dict[str, Any]:
    """生成默认权限条目。"""
    return {
        "enabled": True,
        "level": level,
        "scene": scene,
        "whitelist": {"users": [], "groups": []},
        "blacklist": {"users": [], "groups": []},
    }
