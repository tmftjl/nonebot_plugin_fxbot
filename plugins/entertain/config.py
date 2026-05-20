"""娱乐插件配置。"""

from __future__ import annotations

from typing import Any

from ...config import get_manager
from .ui_schema import DEFAULTS

REG = get_manager().register("entertain", DEFAULTS, clean_extra=True)


def get_config() -> dict[str, Any]:
    """获取娱乐插件完整配置。"""
    return REG.load()


def cfg_reg_time() -> dict[str, Any]:
    """获取 QQ 注册时间配置。"""
    return get_config()["reg_time"]


def cfg_api_urls() -> dict[str, Any]:
    """获取娱乐插件 API 地址配置。"""
    return get_config()["api_urls"]


def cfg_music() -> dict[str, Any]:
    """获取点歌配置。"""
    return get_config()["music"]


def cfg_box() -> dict[str, Any]:
    """获取开盒配置。"""
    return get_config()["box"]
