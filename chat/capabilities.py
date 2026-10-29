"""MaiBot capabilities 配置：工具结果整理与子代理开关。"""

from __future__ import annotations

from typing import Any

from ..config import SYSTEM_DEFAULTS, get_manager as get_config_manager

DEFAULT_CONDENSE_THRESHOLD = 3000


def capabilities_config() -> dict[str, Any]:
    """返回带默认值的 capabilities；配置异常时按全部关闭处理。"""
    merged = dict(SYSTEM_DEFAULTS["chat"]["capabilities"])
    try:
        value = get_config_manager().get_system()["chat"].get("capabilities")
    except Exception:
        value = None
    if isinstance(value, dict):
        merged.update(value)
    return merged


def condense_threshold() -> int:
    """工具结果整理阈值；越界时退回默认值。"""
    try:
        value = int(capabilities_config().get("tool_result_condense_threshold", DEFAULT_CONDENSE_THRESHOLD))
    except (TypeError, ValueError):
        value = DEFAULT_CONDENSE_THRESHOLD
    return min(max(value, 500), 30000)
