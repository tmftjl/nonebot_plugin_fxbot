"""会员门禁判定逻辑和缓存。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from nonebot import get_driver, logger

from ..config import get_manager as get_config_manager

from .models import MembershipGroup
from .service import membership_service


def _now_utc() -> datetime:
    """返回当前 UTC 时间。"""
    return datetime.now(timezone.utc)


def _as_utc(value: datetime | None) -> datetime | None:
    """将 datetime 统一转换为 UTC aware 对象。"""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _is_superuser(user_id: str) -> bool:
    """判断用户是否为 SUPERUSER。"""
    try:
        superusers = {str(item) for item in get_driver().config.superusers or []}
    except Exception:
        superusers = set()
    return str(user_id) in superusers


def _bot_admins() -> set[str]:
    """读取 bot_admin 用户列表。"""
    try:
        cfg = get_config_manager().get_system()
        permission_cfg = cfg.get("permission") if isinstance(cfg.get("permission"), dict) else {}
        value = permission_cfg.get("bot_admins", [])
        if isinstance(value, (list, tuple, set)):
            return {str(item) for item in value if item is not None}
    except Exception:
        return set()
    return set()


@dataclass(frozen=True)
class MembershipDecision:
    """会员门禁决策结果。"""

    allowed: bool
    reason: str
    expires_at: datetime | None = None
    status: str = ""


class MembershipGuard:
    """群会员门禁。"""

    def __init__(self) -> None:
        self._cache: dict[str, MembershipDecision] = {}

    async def check_membership(
        self,
        group_id: str,
        user_id: str,
        *,
        bot_id: str | None = None,
    ) -> tuple[bool, str]:
        """检查群会员资格。"""
        decision = await self.check_membership_detail(group_id, user_id, bot_id=bot_id)
        return decision.allowed, decision.reason

    async def check_membership_detail(
        self,
        group_id: str,
        user_id: str,
        *,
        bot_id: str | None = None,
    ) -> MembershipDecision:
        """检查群会员资格并返回详细结果。"""
        gid = str(group_id or "").strip()
        uid = str(user_id or "").strip()
        if not gid:
            return MembershipDecision(False, "missing_group_id")
        if uid and _is_superuser(uid):
            return MembershipDecision(True, "exempt_superuser")
        if uid and uid in _bot_admins():
            return MembershipDecision(True, "exempt_bot_admin")

        cached = self._cache.get(gid)
        if cached is not None:
            return cached

        decision = await self._load_from_db(gid)
        self._cache[gid] = decision
        return decision

    async def invalidate(self, group_id: str) -> None:
        """刷新指定群缓存。"""
        gid = str(group_id or "").strip()
        if not gid:
            return
        self._cache.pop(gid, None)
        self._cache[gid] = await self._load_from_db(gid)

    async def add_to_cache(
        self,
        group_id: str,
        *,
        allowed: bool,
        reason: str,
        expires_at: datetime | None = None,
        status: str = "",
    ) -> None:
        """手动写入指定群缓存。"""
        gid = str(group_id or "").strip()
        if gid:
            self._cache[gid] = MembershipDecision(allowed, reason, expires_at, status)

    async def reload_all_cache(self) -> None:
        """重新加载所有会员群缓存。"""
        self._cache.clear()
        try:
            rows = await MembershipGroup.select_rows()
        except Exception as exc:
            logger.opt(exception=True).warning(f"[MembershipGuard] 重载缓存失败: {exc}")
            return
        for row in rows:
            group_id = str(row.group_id)
            self._cache[group_id] = self._decision_from_record(row)

    async def _load_from_db(self, group_id: str) -> MembershipDecision:
        """从数据库加载会员状态，异常时 fail-closed。"""
        try:
            record = await membership_service.get_group(group_id)
        except Exception as exc:
            logger.opt(exception=True).warning(
                f"[MembershipGuard] DB 查询失败，按 fail-closed 拒绝: group={group_id} err={exc}"
            )
            return MembershipDecision(False, "db_error")
        if record is None:
            return MembershipDecision(False, "not_registered")
        return self._decision_from_record(record)

    def _decision_from_record(self, record: MembershipGroup) -> MembershipDecision:
        """根据会员记录生成门禁决策。"""
        status = str(record.status or "")
        expires_at = _as_utc(record.expires_at)
        if status != "active":
            return MembershipDecision(False, f"status_{status}", expires_at, status)
        if expires_at is None:
            return MembershipDecision(True, "active_no_expiry", None, status)
        if expires_at <= _now_utc():
            return MembershipDecision(False, "expired", expires_at, status)
        return MembershipDecision(True, "active", expires_at, status)


membership_guard = MembershipGuard()
