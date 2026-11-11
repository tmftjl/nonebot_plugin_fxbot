"""群聊日报配置。"""

from __future__ import annotations

from typing import Any

from ...config import get_manager
from .ui_schema import DEFAULTS


def validate_config(value: dict[str, Any]) -> None:
    """拒绝把非布尔值当成开关，避免字符串 "false" 静默启用。"""
    for group in ("basic", "auto"):
        if group in value and not isinstance(value[group], dict):
            raise ValueError(f"{group} 必须是对象")
    basic = value.get("basic") or {}
    for key in ("enabled", "filter_self"):
        if key in basic and not isinstance(basic[key], bool):
            raise ValueError(f"basic.{key} 必须是布尔值")


REG = get_manager().register("group_analysis", DEFAULTS, clean_extra=True, validator=validate_config)


def get_config() -> dict[str, Any]:
    """每次生成前重新读取，保存后下一份日报生效。"""
    return REG.load()


def basic_config() -> dict[str, Any]:
    return get_config()["basic"]


def auto_config() -> dict[str, Any]:
    return get_config()["auto"]


def is_enabled() -> bool:
    return basic_config()["enabled"] is True


def allowed_groups() -> set[str]:
    """空列表表示所有群都允许。"""
    return {str(item).strip() for item in basic_config().get("groups") or [] if str(item).strip()}


def group_allowed(group_id: str) -> bool:
    groups = allowed_groups()
    return not groups or str(group_id) in groups


def auto_groups() -> list[str]:
    return [str(item).strip() for item in auto_config().get("auto_groups") or [] if str(item).strip()]


def bounded_days(value: int | None) -> int:
    """手动天数限制在 1 到 max_days，避免回溯过久拖垮协议端。"""
    config = basic_config()
    maximum = max(1, int(config.get("max_days") or 1))
    days = int(value if value is not None else config.get("days") or 1)
    return min(max(days, 1), maximum)
