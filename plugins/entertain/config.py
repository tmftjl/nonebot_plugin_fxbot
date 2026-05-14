"""娱乐插件配置。"""

from __future__ import annotations

from typing import Any

from ...config import get_manager

DEFAULTS: dict[str, Any] = {
    "music": {
        "api_base": "https://api.vkeys.cn",
        "provider_default": "tencent",
        "search_num": 20,
        "quality": 4,
    },
    "reg_time": {
        "qq_reg_time_api_url": "https://api.s01s.cn/API/zcsj/",
        "qq_reg_time_api_key": "",
    },
    "api_urls": {
        "sick_quote_api": "https://oiapi.net/API/SickL/",
        "doro_api": "https://doro-api.hxxn.cc/get",
    },
}

REG = get_manager().register("entertain", DEFAULTS, clean_extra=True)


def get_config() -> dict[str, Any]:
    """获取娱乐插件完整配置。"""
    return REG.load()


def cfg_reg_time() -> dict[str, Any]:
    """获取 QQ 注册时间配置。"""
    return get_config().get("reg_time", {})


def cfg_api_urls() -> dict[str, Any]:
    """获取娱乐插件 API 地址配置。"""
    return get_config().get("api_urls", {})


def cfg_music() -> dict[str, Any]:
    """获取点歌配置。"""
    return get_config().get("music", {})
