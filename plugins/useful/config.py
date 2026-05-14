"""实用工具插件配置。"""

from __future__ import annotations

from typing import Any

from ...config import get_manager

DEFAULTS: dict[str, Any] = {
    "taffy": {
        "api_url": "http://127.0.0.1:8899/stats/api",
        "username": "",
        "password": "",
    },
}

REG = get_manager().register("useful", DEFAULTS, clean_extra=True)


def get_config() -> dict[str, Any]:
    """获取实用工具完整配置。"""
    return REG.load()


def cfg_taffy() -> dict[str, Any]:
    """获取 Taffy 查询配置。"""
    return get_config().get("taffy", {})
