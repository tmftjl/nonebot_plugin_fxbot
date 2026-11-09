"""无限二代热更新配置。"""

from __future__ import annotations

from typing import Any

from ...config import get_manager
from .ui_schema import DEFAULTS


def validate_config(value: dict[str, Any]) -> None:
    """拒绝把字符串等配置值误当成启用开关。"""
    for key in ("enabled", "direct_llm"):
        if key in value and not isinstance(value[key], bool):
            raise ValueError(f"{key} 必须是布尔值")


REG = get_manager().register("infinite_gen2", DEFAULTS, clean_extra=True, validator=validate_config)


def is_enabled() -> bool:
    """每次生成读取开关，保存后下一次回复生效。"""
    return REG.load()["enabled"] is True


def direct_llm_enabled() -> bool:
    """直聊开关独立于提示词注入，保存后下一次轮次生效。"""
    return REG.load()["direct_llm"] is True
