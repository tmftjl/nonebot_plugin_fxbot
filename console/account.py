"""控制台账户：单账号密码登录与首次登录改密标记。"""

from __future__ import annotations

import hmac
import json
import string
import hashlib
import secrets
from typing import Any
from pathlib import Path

from nonebot import logger

from ..utils.paths import config_dir

USERNAME = "root"
MIN_PASSWORD = 8
_ITERATIONS = 200_000
_ALPHABET = string.ascii_letters + string.digits


def _path() -> Path:
    return config_dir("console") / "account.json"


def _hash(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("ascii"), _ITERATIONS).hex()


def _read() -> dict[str, Any]:
    path = _path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("[Console] 控制台账户文件损坏，将重新生成初始账户")
        return {}
    return data if isinstance(data, dict) else {}


def _write(data: dict[str, Any]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _new_password(length: int = 14) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


def ensure_account() -> dict[str, Any]:
    """账户不存在时创建 root 并把随机初始密码打到启动日志。"""
    data = _read()
    if data.get("username") and data.get("salt") and data.get("password_hash"):
        return data
    password = _new_password()
    salt = secrets.token_hex(16)
    data = {
        "username": USERNAME,
        "salt": salt,
        "password_hash": _hash(password, salt),
        "must_change_password": True,
    }
    _write(data)
    logger.warning(f"[Console] 已生成控制台初始账户 {USERNAME}，初始密码：{password}（首次登录后请立即修改）")
    return data


def verify(username: str, password: str) -> bool:
    """校验用户名密码。"""
    data = ensure_account()
    expected_name = str(data.get("username") or "")
    if not hmac.compare_digest(str(username).strip(), expected_name):
        return False
    expected = str(data.get("password_hash") or "")
    return hmac.compare_digest(_hash(str(password), str(data.get("salt") or "")), expected)


def profile() -> dict[str, Any]:
    """登录态需要的账户信息。"""
    data = ensure_account()
    return {
        "username": str(data.get("username") or USERNAME),
        "must_change_password": bool(data.get("must_change_password")),
    }


def change_password(old_password: str, new_password: str) -> None:
    """校验旧密码后写入新密码；首次改密同时清掉提醒标记。"""
    data = ensure_account()
    if not verify(str(data.get("username") or ""), old_password):
        raise ValueError("原密码不正确")
    if len(new_password) < MIN_PASSWORD:
        raise ValueError(f"新密码至少 {MIN_PASSWORD} 位")
    if new_password == old_password:
        raise ValueError("新密码不能与原密码相同")
    salt = secrets.token_hex(16)
    data.update(
        salt=salt,
        password_hash=_hash(new_password, salt),
        must_change_password=False,
    )
    _write(data)
