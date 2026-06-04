"""洛克王国插件配置。"""

from __future__ import annotations

from typing import Any

from ...config import get_manager
from .ui_schema import DEFAULTS

REG = get_manager().register("rocom", DEFAULTS, clean_extra=True)


def get_config() -> dict[str, Any]:
    """获取洛克王国插件完整配置。"""
    return REG.load()


def cfg_merchant() -> dict[str, Any]:
    """获取远行商人推送配置。"""
    return get_config()["merchant"]
