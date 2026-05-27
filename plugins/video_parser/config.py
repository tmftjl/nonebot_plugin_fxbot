"""视频解析插件配置。"""

from __future__ import annotations

from typing import Any

from ...config import get_manager
from .ui_schema import DEFAULTS

REG = get_manager().register("video_parser", DEFAULTS, clean_extra=True)


def get_config() -> dict[str, Any]:
    """获取视频解析完整配置。"""
    return REG.load()


def save_config(data: dict[str, Any]) -> None:
    """保存视频解析完整配置。"""
    REG.save(data)


def cfg_general() -> dict[str, Any]:
    """获取通用配置。"""
    return get_config()["general"]


def cfg_platforms() -> dict[str, bool]:
    """获取平台开关。"""
    return {str(k): bool(v) for k, v in get_config()["platforms"].items()}


def cfg_network() -> dict[str, Any]:
    """获取网络配置。"""
    return get_config()["network"]


def is_global_enabled() -> bool:
    """判断全局解析是否启用。"""
    return bool(cfg_general().get("global_enabled", True))


def set_global_enabled(enabled: bool) -> None:
    """设置全局解析开关。"""
    data = get_config()
    data.setdefault("general", {})["global_enabled"] = bool(enabled)
    save_config(data)
