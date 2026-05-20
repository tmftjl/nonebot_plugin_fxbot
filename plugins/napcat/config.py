"""NapCat 插件配置。"""

from __future__ import annotations

from typing import Any

from ...config import get_manager
from .ui_schema import DEFAULTS

REG = get_manager().register("napcat", DEFAULTS, clean_extra=True)


def get_config() -> dict[str, Any]:
    """获取 NapCat 完整配置。"""
    return REG.load()


def save_config(cfg: dict[str, Any]) -> None:
    """保存 NapCat 配置。"""
    REG.save(cfg)


def cfg_image_display() -> dict[str, Any]:
    """获取图片外显配置。"""
    return get_config()["image_display"]
