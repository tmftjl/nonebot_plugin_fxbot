"""会员控制台路由。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ...membership.guard import membership_guard
from ...membership.service import MembershipError, membership_service
from ..auth import bearer_auth

router = APIRouter(prefix="/membership", tags=["fxbot-membership"], dependencies=[Depends(bearer_auth)])


@router.post("/codes")
async def generate_code(payload: dict[str, Any]) -> dict[str, Any]:
    """生成续费码。"""
    try:
        expires_at = payload.get("expires_at")
        row = await membership_service.generate_code(
            duration_value=int(payload.get("duration_value")),
            duration_unit=str(payload.get("duration_unit")),
            max_use=int(payload.get("max_use", 1)),
            expires_at=datetime.fromisoformat(expires_at) if expires_at else None,
        )
    except (ValueError, TypeError, MembershipError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return row.model_dump(mode="json")


@router.post("/groups/{group_id}/extend")
async def extend_group(group_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """手动延期会员群。"""
    try:
        result = await membership_service.extend_group(
            group_id,
            duration_value=int(payload.get("duration_value")),
            duration_unit=str(payload.get("duration_unit")),
            operator_user_id=str(payload.get("operator_user_id") or "") or None,
            code="console",
        )
        await membership_guard.invalidate(group_id)
    except (ValueError, TypeError, MembershipError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result.group.model_dump(mode="json")


@router.put("/groups/{group_id}")
async def upsert_group(group_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """新增或更新会员群。"""
    expires_at = payload.get("expires_at")
    try:
        row = await membership_service.upsert_group(
            group_id,
            expires_at=datetime.fromisoformat(expires_at) if expires_at else None,
            status=str(payload.get("status") or "active"),
            managed_by_bot=str(payload.get("managed_by_bot") or "") or None,
            remark=payload.get("remark"),
        )
        await membership_guard.invalidate(group_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return row.model_dump(mode="json")
