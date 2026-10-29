"""控制台账户登录路由。"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, APIRouter, HTTPException

from .. import account
from ..auth import bearer_auth, get_console_token, rotate_console_token

public_router = APIRouter(prefix="/auth", tags=["fxbot-console-auth"])
router = APIRouter(prefix="/auth", tags=["fxbot-console-auth"], dependencies=[Depends(bearer_auth)])


@public_router.post("/login")
async def login(payload: dict[str, Any]) -> dict[str, Any]:
    """用户名密码换取控制台访问 Token。"""
    username = str(payload.get("username") or "")
    password = str(payload.get("password") or "")
    if not account.verify(username, password):
        raise HTTPException(status_code=401, detail="用户名或密码不正确")
    return {"token": get_console_token(), **account.profile()}


@router.get("/me")
async def get_profile() -> dict[str, Any]:
    """当前账户信息；首次登录提醒据此判断。"""
    return account.profile()


@router.put("/password")
async def update_password(payload: dict[str, Any]) -> dict[str, Any]:
    """修改密码并换发新的访问 Token，旧 Token 立即失效。"""
    try:
        account.change_password(str(payload.get("old_password") or ""), str(payload.get("new_password") or ""))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"token": rotate_console_token(), **account.profile()}
