"""表情包插件配置。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Optional

from ...config import get_manager
from ...utils.paths import data_dir
from .ui_schema import DEFAULTS

REG = get_manager().register("memes", DEFAULTS, clean_extra=True)


@dataclass(frozen=True)
class MemeListImageConfig:
    """表情列表图片配置。"""

    sort_by: str = "keywords"
    sort_reverse: bool = False
    text_template: str = "{keywords}"
    add_category_icon: bool = True
    label_new_timedelta: timedelta = timedelta(days=30)
    label_hot_threshold: int = 21
    label_hot_days: int = 7


@dataclass(frozen=True)
class MemeParamsMismatchPolicy:
    """参数不匹配处理策略。"""

    too_much_text: str = "ignore"
    too_few_text: str = "ignore"
    too_much_image: str = "ignore"
    too_few_image: str = "ignore"


@dataclass(frozen=True)
class MultipleImageConfig:
    """多图发送配置。"""

    direct_send_threshold: int = 10
    send_zip_file: bool = True
    send_forward_msg: bool = False


def get_config() -> dict[str, Any]:
    """获取表情包完整配置。"""
    return REG.load()


def cfg_generator() -> dict[str, Any]:
    """获取生成器配置。"""
    return get_config()["generator"]


def cfg_behavior() -> dict[str, Any]:
    """获取行为配置。"""
    return get_config()["behavior"]


def cfg_list_image() -> dict[str, Any]:
    """获取列表图片配置。"""
    return get_config()["list_image"]


def cfg_mismatch_policy() -> dict[str, Any]:
    """获取参数不匹配策略配置。"""
    return get_config()["mismatch_policy"]


def cfg_multiple_image() -> dict[str, Any]:
    """获取多图配置。"""
    return get_config()["multiple_image"]


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


def cfg_use_sender_when_no_image() -> bool:
    """缺图时是否使用发送者头像。"""
    return bool(cfg_behavior().get("use_sender_when_no_image", False))


def cfg_use_default_when_no_text() -> bool:
    """缺字时是否使用默认文案。"""
    return bool(cfg_behavior().get("use_default_when_no_text", False))


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


def cfg_list_image_config() -> MemeListImageConfig:
    """构造表情列表图片配置。"""
    data = cfg_list_image()
    return MemeListImageConfig(
        sort_by=str(data.get("sort_by") or "keywords"),
        sort_reverse=bool(data.get("sort_reverse", False)),
        text_template=str(data.get("text_template") or "{keywords}"),
        add_category_icon=bool(data.get("add_category_icon", True)),
        label_new_timedelta=timedelta(days=int(data.get("label_new_days", 30) or 30)),
        label_hot_threshold=int(data.get("label_hot_threshold", 21) or 21),
        label_hot_days=int(data.get("label_hot_days", 7) or 7),
    )


def cfg_mismatch_policy_config() -> MemeParamsMismatchPolicy:
    """构造参数不匹配处理策略。"""
    data = cfg_mismatch_policy()
    return MemeParamsMismatchPolicy(
        too_much_text=str(data.get("too_much_text") or "ignore"),
        too_few_text=str(data.get("too_few_text") or "ignore"),
        too_much_image=str(data.get("too_much_image") or "ignore"),
        too_few_image=str(data.get("too_few_image") or "ignore"),
    )


def cfg_multiple_image_config() -> MultipleImageConfig:
    """构造多图发送配置。"""
    data = cfg_multiple_image()
    return MultipleImageConfig(
        direct_send_threshold=int(data.get("direct_send_threshold", 10) or 10),
        send_zip_file=bool(data.get("send_zip_file", True)),
        send_forward_msg=bool(data.get("send_forward_msg", False)),
    )


ban_path = str(data_dir("memes") / "ban")
