"""洛克王国插件配置。"""

from __future__ import annotations

from typing import Any

from ...config import get_manager
from .ui_schema import DEFAULTS

REG = get_manager().register("rocom", DEFAULTS, clean_extra=True)


def get_config() -> dict[str, Any]:
    """获取洛克王国插件完整配置。"""
    return REG.load()


def cfg_resources() -> dict[str, Any]:
    """获取运行时资源下载配置。"""
    return get_config()["resources"]
