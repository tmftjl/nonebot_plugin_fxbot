"""鸣潮表情插件配置。"""

from __future__ import annotations

from typing import Any

from ...config import get_manager
from .ui_schema import DEFAULTS

REG = get_manager().register("wuwa_emoji", DEFAULTS, clean_extra=True)


def get_config() -> dict[str, Any]:
    """获取鸣潮表情完整配置。"""
    return REG.load()


def cfg_api_base() -> str:
    """获取接口前缀地址。"""
    return str(get_config().get("api_base") or DEFAULTS["api_base"]).rstrip("/")


def cfg_api_token() -> str:
    """获取接口 Token，未配置时返回空串。"""
    return str(get_config().get("api_token") or "").strip()


def cfg_character_cache_ttl() -> int:
    """获取角色列表缓存有效秒数，非法值回退为默认值。"""
    try:
        value = int(get_config().get("character_cache_ttl", DEFAULTS["character_cache_ttl"]))
    except (TypeError, ValueError):
        return int(DEFAULTS["character_cache_ttl"])
    return max(value, 0)
