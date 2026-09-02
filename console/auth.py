"""6 字符以上随机 token 生成和 Bearer 认证。"""

from __future__ import annotations

import secrets
from typing import Any

from fastapi import HTTPException, Request, status

from ..config import SYSTEM_DEFAULTS, get_manager as get_config_manager

_TOKEN_BYTES = 32
_MIN_TOKEN_LENGTH = 6


def _system_proxy():
    """获取系统配置代理。"""
    manager = get_config_manager()
    return manager.register("system", SYSTEM_DEFAULTS)


def get_console_token() -> str:
    """读取控制台 token，不存在时自动生成。"""
    manager = get_config_manager()
    cfg = manager.get_system()
    console_cfg = cfg["console"]
    token = str(console_cfg["token"] or "")
    if len(token) >= _MIN_TOKEN_LENGTH:
        return token
    return rotate_console_token()


def rotate_console_token() -> str:
    """生成并保存新的控制台 token。"""
    manager = get_config_manager()
    cfg = manager.get_system()
    console_cfg = cfg["console"]
    token = secrets.token_urlsafe(_TOKEN_BYTES)
    console_cfg["token"] = token
    _system_proxy().save(cfg)
    return token


def verify_bearer_token(request: Request) -> None:
    """校验 Bearer token。"""
    auth_header = request.headers.get("authorization", "")
    prefix = "Bearer "
    if not auth_header.startswith(prefix):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="缺少认证 token")
    token = auth_header[len(prefix) :].strip()
    if not secrets.compare_digest(token, get_console_token()):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="认证 token 无效")


async def bearer_auth(request: Request) -> None:
    """FastAPI 依赖形式的 Bearer 认证。"""
    verify_bearer_token(request)
