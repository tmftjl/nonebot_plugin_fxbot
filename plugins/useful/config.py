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
    "waves_analyze": {
        "api_url": "https://scoreecho.loping151.site/score",
        "token": "f3e6d1d382925f0c63bd296e3e92a314",
    },
}

REG = get_manager().register("useful", DEFAULTS, clean_extra=True)


def get_config() -> dict[str, Any]:
    """获取实用工具完整配置。"""
    return REG.load()


def cfg_taffy() -> dict[str, Any]:
    """获取 Taffy 查询配置。"""
    return get_config().get("taffy", {})


def cfg_waves_analyze() -> dict[str, Any]:
    """获取鸣潮评分配置。"""
    return get_config().get("waves_analyze", {})
