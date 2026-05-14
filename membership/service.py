"""会员系统业务逻辑。"""

from __future__ import annotations

import secrets
import string
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from ..db import get_session_maker

from .models import MembershipGroup, RenewCode, RenewRecord, utc_now


class MembershipError(RuntimeError):
    """会员业务错误。"""


@dataclass
class RedeemResult:
    """续费码兑换结果。"""

    group: MembershipGroup
    record: RenewRecord
    before_expires_at: datetime | None
    after_expires_at: datetime | None


def _duration_delta(value: int, unit: str) -> timedelta:
    """将续费时长转换为 timedelta。"""
    if value <= 0:
        raise MembershipError("续费时长必须大于 0")
    unit_key = str(unit).lower()
    if unit_key in {"day", "days", "d"}:
        return timedelta(days=value)
    if unit_key in {"month", "months", "m"}:
        return timedelta(days=value * 30)
    if unit_key in {"year", "years", "y"}:
        return timedelta(days=value * 365)
    raise MembershipError(f"不支持的续费单位: {unit}")


def _generate_code(length: int = 16) -> str:
    """生成随机续费码。"""
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


class MembershipService:
    """会员业务服务。"""

    async def get_group(self, group_id: str, *, session: AsyncSession | None = None) -> MembershipGroup | None:
        """按群号获取会员群。"""
        owns_session = session is None
        if owns_session:
            maker = get_session_maker()
            async with maker() as new_session:
                return await self.get_group(group_id, session=new_session)
        assert session is not None
        result = await session.execute(
            select(MembershipGroup).where(MembershipGroup.group_id == str(group_id))
        )
        return result.scalar_one_or_none()

    async def upsert_group(
        self,
        group_id: str,
        *,
        expires_at: datetime | None = None,
        status: str = "active",
        managed_by_bot: str | None = None,
        remark: str | None = None,
    ) -> MembershipGroup:
        """新增或更新会员群。"""
        maker = get_session_maker()
        async with maker() as session:
            group = await self.get_group(group_id, session=session)
            if group is None:
                group = MembershipGroup(group_id=str(group_id))
                session.add(group)
            group.status = status
            group.expires_at = expires_at
            group.managed_by_bot = managed_by_bot
            group.remark = remark
            group.updated_at = utc_now()
            await session.commit()
            await session.refresh(group)
            return group

    async def extend_group(
        self,
        group_id: str,
        *,
        duration_value: int,
        duration_unit: str,
        operator_user_id: str | None = None,
        code: str = "",
        session: AsyncSession | None = None,
    ) -> RedeemResult:
        """为会员群延期。"""
        owns_session = session is None
        if owns_session:
            maker = get_session_maker()
            async with maker() as new_session:
                result = await self.extend_group(
                    group_id,
                    duration_value=duration_value,
                    duration_unit=duration_unit,
                    operator_user_id=operator_user_id,
                    code=code,
                    session=new_session,
                )
                await new_session.commit()
                return result

        assert session is not None
        now = utc_now()
        delta = _duration_delta(duration_value, duration_unit)
        group = await self.get_group(group_id, session=session)
        if group is None:
            group = MembershipGroup(group_id=str(group_id), status="active")
            session.add(group)
        before = group.expires_at
        base = before if before and before > now else now
        after = base + delta
        group.status = "active"
        group.expires_at = after
        group.updated_at = now

        record = RenewRecord(
            code=code,
            group_id=str(group_id),
            operator_user_id=str(operator_user_id) if operator_user_id else None,
            before_expires_at=before,
            after_expires_at=after,
        )
        session.add(record)
        return RedeemResult(group=group, record=record, before_expires_at=before, after_expires_at=after)

    async def generate_code(
        self,
        *,
        duration_value: int,
        duration_unit: str,
        max_use: int = 1,
        expires_at: datetime | None = None,
        code_length: int = 16,
    ) -> RenewCode:
        """生成续费码。"""
        _duration_delta(duration_value, duration_unit)
        if max_use <= 0:
            raise MembershipError("最大使用次数必须大于 0")

        maker = get_session_maker()
        async with maker() as session:
            for _ in range(20):
                code = _generate_code(code_length)
                exists = await session.execute(select(RenewCode).where(RenewCode.code == code))
                if exists.scalar_one_or_none() is None:
                    row = RenewCode(
                        code=code,
                        duration_value=duration_value,
                        duration_unit=duration_unit,
                        max_use=max_use,
                        expires_at=expires_at,
                    )
                    session.add(row)
                    await session.commit()
                    await session.refresh(row)
                    return row
        raise MembershipError("续费码生成失败，请重试")

    async def redeem_code(
        self,
        code: str,
        group_id: str,
        *,
        operator_user_id: str | None = None,
    ) -> RedeemResult:
        """兑换续费码。"""
        maker = get_session_maker()
        async with maker() as session:
            result = await session.execute(select(RenewCode).where(RenewCode.code == str(code).strip()))
            renew_code = result.scalar_one_or_none()
            if renew_code is None:
                raise MembershipError("续费码不存在")
            now = utc_now()
            if renew_code.status != "active":
                raise MembershipError("续费码不可用")
            if renew_code.expires_at and renew_code.expires_at <= now:
                raise MembershipError("续费码已过期")
            if renew_code.used_count >= renew_code.max_use:
                raise MembershipError("续费码使用次数已耗尽")

            redeem_result = await self.extend_group(
                group_id,
                duration_value=renew_code.duration_value,
                duration_unit=renew_code.duration_unit,
                operator_user_id=operator_user_id,
                code=renew_code.code,
                session=session,
            )
            renew_code.used_count += 1
            renew_code.updated_at = now
            if renew_code.used_count >= renew_code.max_use:
                renew_code.status = "used"
            await session.commit()
            await session.refresh(redeem_result.group)
            await session.refresh(redeem_result.record)
            return redeem_result


membership_service = MembershipService()
