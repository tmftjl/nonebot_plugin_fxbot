"""无限二代热更新配置。"""

from __future__ import annotations

from typing import Any

from ...config import get_manager
from .ui_schema import DEFAULTS


def validate_config(value: dict[str, Any]) -> None:
    """拒绝把字符串等配置值误当成启用开关。"""
    if "enabled" in value and not isinstance(value["enabled"], bool):
        raise ValueError("enabled 必须是布尔值")


REG = get_manager().register("infinite_gen2", DEFAULTS, clean_extra=True, validator=validate_config)


def is_enabled() -> bool:
    """每次生成读取开关，保存后下一次回复生效。"""
    return REG.load()["enabled"] is True
