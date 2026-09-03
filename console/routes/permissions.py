"""权限配置路由和热重载。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ...permission import get_storage
from ..auth import bearer_auth

router = APIRouter(
    prefix="/permissions",
    tags=["fxbot-permissions"],
    dependencies=[Depends(bearer_auth)],
)


@router.get("")
async def get_permissions() -> dict[str, Any]:
    """读取权限配置。"""
    return get_storage().load()


@router.put("")
async def update_permissions(payload: dict[str, Any]) -> dict[str, Any]:
    """保存权限配置。"""
    try:
        get_storage().save(payload)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": True}
