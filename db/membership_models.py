"""会员相关数据库模型。"""

from __future__ import annotations

from typing import Any

from nonebot import logger
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import Field, delete

from .base_models import BaseIDModel, exec_list, with_session

# 给旧表补 remark 字段
exec_list.extend([
    "ALTER TABLE membership ADD COLUMN remark TEXT",
])


class Membership(BaseIDModel, table=True):
    """会员记录。"""

    group_id: str = Field(index=True, unique=True, nullable=False, title="group_id")
    expiry: str | None = Field(default=None, nullable=True, title="expiry")
    last_renewed_by: str | None = Field(default=None, nullable=True, title="last_renewed_by")
    renewal_code_used: str | None = Field(default=None, nullable=True, title="renewal_code_used")
    managed_by_bot: str | None = Field(default=None, nullable=True, title="managed_by_bot")
    status: str = Field(default="active", nullable=False, title="status")
    last_reminder_on: str | None = Field(default=None, nullable=True, title="last_reminder_on")
    expired_at: str | None = Field(default=None, nullable=True, title="expired_at")
    remark: str | None = Field(default=None, nullable=True, title="remark")

    @classmethod
    async def all(cls) -> list["Membership"]:
        """查询全部会员记录。"""
        return await cls.select_rows()  # type: ignore[return-value]

    @classmethod
    @with_session
    async def replace_all(
        cls,
        session: AsyncSession,
        rows: list[dict[str, Any]],
    ) -> None:
        """用给定数据整体替换会员表。"""
        async with session.begin():
            await session.execute(delete(cls))
            for row in rows:
                session.add(cls(**row))


class GeneratedCode(BaseIDModel, table=True):
    """可兑换会员码。"""

    code: str = Field(index=True, unique=True, nullable=False, title="code")
    length: int = Field(nullable=False, title="length")
    unit: str = Field(nullable=False, title="unit")
    generated_time: str = Field(nullable=False, title="generated_time")
    max_use: int = Field(default=1, nullable=False, title="max_use")
    used_count: int = Field(default=0, nullable=False, title="used_count")
    expire_at: str | None = Field(default=None, nullable=True, title="expire_at")

    @classmethod
    async def all(cls) -> list["GeneratedCode"]:
        """查询全部会员码记录。"""
        return await cls.select_rows()  # type: ignore[return-value]

    @classmethod
    @with_session
    async def replace_all(
        cls,
        session: AsyncSession,
        rows: list[dict[str, Any]],
    ) -> None:
        """用给定数据整体替换会员码表。"""
        async with session.begin():
            await session.execute(delete(cls))
            for row in rows:
                session.add(cls(**row))


async def read_snapshot() -> dict[str, Any]:
    """把全部会员数据读成快照。"""
    data: dict[str, Any] = {"generatedCodes": {}}

    mem_rows = await Membership.all()
    for row in mem_rows:
        data[row.group_id] = {
            "id": row.id,
            "group_id": row.group_id,
            "expiry": row.expiry,
            "last_renewed_by": row.last_renewed_by,
            "renewal_code_used": row.renewal_code_used,
            "managed_by_bot": row.managed_by_bot,
            "status": row.status,
            "last_reminder_on": row.last_reminder_on,
            "expired_at": row.expired_at,
            "remark": row.remark,
        }

    codes = await GeneratedCode.all()
    gen_map: dict[str, Any] = {}
    for row in codes:
        gen_map[row.code] = {
            "length": row.length,
            "unit": row.unit,
            "generated_time": row.generated_time,
            "max_use": row.max_use,
            "used_count": row.used_count,
            "expire_at": row.expire_at,
        }
    data["generatedCodes"] = gen_map

    return data


async def write_snapshot(obj: dict[str, Any]) -> None:
    """把快照写回数据库。"""
    mem_rows: list[dict[str, Any]] = []
    for key, value in obj.items():
        if key == "generatedCodes" or not isinstance(value, dict):
            continue

        def _s(val: Any) -> str | None:
            if val is None:
                return None
            try:
                return str(val)
            except Exception:
                return None

        mem_rows.append(
            {
                "group_id": str(value.get("group_id") or key),
                "expiry": _s(value.get("expiry")),
                "last_renewed_by": _s(value.get("last_renewed_by")),
                "renewal_code_used": _s(value.get("renewal_code_used")),
                "managed_by_bot": _s(value.get("managed_by_bot")),
                "status": str(value.get("status") or "active"),
                "last_reminder_on": _s(value.get("last_reminder_on")),
                "expired_at": _s(value.get("expired_at")),
                "remark": _s(value.get("remark")),
            }
        )

    code_rows: list[dict[str, Any]] = []
    gen_map = obj.get("generatedCodes") or {}
    if isinstance(gen_map, dict):
        for code, rec in gen_map.items():
            try:
                sqlite_int64_min = -9223372036854775808
                sqlite_int64_max = 9223372036854775807

                length_val = int(rec.get("length"))
                max_use_val = int(rec.get("max_use", 1) or 1)
                used_count_val = int(rec.get("used_count", 0) or 0)

                def _in_sqlite_range(value: int) -> bool:
                    return sqlite_int64_min <= value <= sqlite_int64_max

                if not (
                    _in_sqlite_range(length_val)
                    and _in_sqlite_range(max_use_val)
                    and _in_sqlite_range(used_count_val)
                ):
                    logger.warning(
                        f"[membership] 跳过无效兑换码记录（整数超出 SQLite 范围）: code={code}, "
                        f"length={length_val}, max_use={max_use_val}, used_count={used_count_val}"
                    )
                    continue

                code_rows.append(
                    {
                        "code": str(code),
                        "length": length_val,
                        "unit": str(rec.get("unit")),
                        "generated_time": str(rec.get("generated_time")),
                        "max_use": max_use_val,
                        "used_count": used_count_val,
                        "expire_at": str(rec.get("expire_at")) if rec.get("expire_at") else None,
                    }
                )
            except Exception:
                continue

    await Membership.replace_all(mem_rows)
    await GeneratedCode.replace_all(code_rows)
