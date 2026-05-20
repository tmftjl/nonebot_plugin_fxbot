"""实用工具插件配置。"""

from __future__ import annotations

from typing import Any

from ...config import get_manager
from .ui_schema import DEFAULTS

REG = get_manager().register("useful", DEFAULTS, clean_extra=True)


def get_config() -> dict[str, Any]:
    """获取实用工具完整配置。"""
    return REG.load()


def cfg_taffy() -> dict[str, Any]:
    """获取 Taffy 查询配置。"""
    return get_config()["taffy"]


def cfg_waves_analyze() -> dict[str, Any]:
    """获取鸣潮评分配置。"""
    return get_config()["waves_analyze"]
