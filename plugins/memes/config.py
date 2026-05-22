"""表情包插件配置。"""

from __future__ import annotations

from typing import Any, Optional

from ...config import get_manager
from ...utils.paths import data_dir
from .ui_schema import DEFAULTS

REG = get_manager().register("memes", DEFAULTS, clean_extra=True)


def get_config() -> dict[str, Any]:
    """获取表情包完整配置。"""
    return REG.load()


def cfg_generator() -> dict[str, Any]:
    """获取生成器配置。"""
    return get_config()["generator"]


def cfg_behavior() -> dict[str, Any]:
    """获取行为配置。"""
    return get_config()["behavior"]


def cfg_base_url() -> str:
    """获取表情生成服务地址。"""
    return str(cfg_generator().get("base_url") or "http://127.0.0.1:2233")


def cfg_command_prefixes() -> Optional[list[str]]:
    """获取命令前缀配置。"""
    prefixes = cfg_generator().get("command_prefixes")
    if prefixes is None:
        return None
    return list(prefixes)


def cfg_disabled_list() -> list[str]:
    """获取全局禁用表情列表。"""
    return list(cfg_generator().get("disabled_list") or [])


def cfg_check_resources_on_startup() -> bool:
    """是否在启动时检查表情资源。"""
    return bool(cfg_generator().get("check_resources_on_startup", True))


def cfg_random_meme_show_info() -> bool:
    """随机表情时是否附带提示信息。"""
    return bool(cfg_behavior().get("random_meme_show_info", True))


def cfg_notice_prob() -> float:
    """随机表情提示概率。"""
    return float(cfg_behavior().get("notice_prob", 0.1) or 0.0)


def cfg_use_gif() -> bool:
    """是否启用 GIF 输出。"""
    return bool(cfg_behavior().get("use_gif", False))


def cfg_use_ban_word() -> bool:
    """是否启用屏蔽词。"""
    return bool(cfg_behavior().get("use_ban_word", True))


ban_path = str(data_dir("memes") / "ban")
