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


def cfg_base_url() -> str:
    """获取表情生成服务地址。"""
    return str(get_config().get("base_url") or "http://127.0.0.1:2233")


def cfg_command_prefixes() -> Optional[list[str]]:
    """获取命令前缀配置。"""
    prefixes = get_config().get("command_prefixes")
    if prefixes is None:
        return None
    return list(prefixes)


def cfg_disabled_list() -> list[str]:
    """获取全局禁用表情列表。"""
    return list(get_config().get("disabled_list") or [])


def cfg_check_resources_on_startup() -> bool:
    """是否在启动时检查表情资源。"""
    return bool(get_config().get("check_resources_on_startup", True))


def cfg_random_meme_show_info() -> bool:
    """随机表情时是否附带提示信息。"""
    return bool(get_config().get("random_meme_show_info", True))


def cfg_notice_prob() -> float:
    """随机表情提示概率。"""
    return float(get_config().get("notice_prob", 0.1) or 0.0)


def cfg_use_gif() -> bool:
    """是否启用 GIF 输出。"""
    return bool(get_config().get("use_gif", False))


def cfg_use_ban_word() -> bool:
    """是否启用屏蔽词。"""
    return bool(get_config().get("use_ban_word", True))


def cfg_whitelist_ids() -> list[str]:
    """获取保护白名单用户 QQ 号列表。"""
    return [str(user_id) for user_id in (get_config().get("whitelist_ids") or [])]


def cfg_protected_memes() -> list[str]:
    """获取需要保护的表情 key 列表。"""
    return [str(meme_key) for meme_key in (get_config().get("protected_memes") or [])]


def save_protection_config(
    whitelist_ids: list[str], protected_memes: list[str]
) -> None:
    """保存表情保护配置。"""
    config = get_config()
    config["whitelist_ids"] = list(whitelist_ids)
    config["protected_memes"] = list(protected_memes)
    REG.save(config)


ban_path = str(data_dir("memes") / "ban")
