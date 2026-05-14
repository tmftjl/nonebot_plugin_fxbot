"""系统配置路由。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ...config import get_manager
from ..auth import bearer_auth

router = APIRouter(prefix="/config", tags=["fxbot-config"], dependencies=[Depends(bearer_auth)])


@router.get("")
async def get_config() -> dict[str, Any]:
    """读取系统配置。"""
    return get_manager().get_system()


@router.put("")
async def update_config(payload: dict[str, Any]) -> dict[str, Any]:
    """保存系统配置。"""
    try:
        proxy = get_manager().register("system", payload)
        proxy.save(payload)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": True}
