"""群聊日报配置。"""

from __future__ import annotations

from typing import Any

from ...config import get_manager
from .ui_schema import DEFAULTS


def validate_config(value: dict[str, Any]) -> None:
    """拒绝把非布尔值当成开关，避免字符串 "false" 静默启用。"""
    for group in ("basic", "auto", "analysis_features"):
        if group in value and not isinstance(value[group], dict):
            raise ValueError(f"{group} 必须是对象")
    basic = value.get("basic") or {}
    for key in ("enabled", "filter_self"):
        if key in basic and not isinstance(basic[key], bool):
            raise ValueError(f"basic.{key} 必须是布尔值")
    if "theme" in basic and basic["theme"] not in ("miku", "atri"):
        raise ValueError("basic.theme 必须是 miku 或 atri")
    features = value.get("analysis_features") or {}
    for key in (
        "topic_analysis_enabled",
        "user_title_analysis_enabled",
        "golden_quote_analysis_enabled",
        "chat_quality_analysis_enabled",
        "keep_original_persona",
        "use_plugin_specific_persona",
    ):
        if key in features and not isinstance(features[key], bool):
            raise ValueError(f"analysis_features.{key} 必须是布尔值")


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


def analysis_features() -> dict[str, Any]:
    return get_config()["analysis_features"]


def resolve_persona(session_id: str = "") -> str:
    """强制指定人格优先，其次继承会话人格；都未开启时不注入。"""
    from ...chat.personas import DEFAULT_PERSONA, get_persona_text, effective_persona_name

    features = analysis_features()
    if features.get("use_plugin_specific_persona"):
        name = str(features.get("plugin_specific_persona_id") or "").strip() or DEFAULT_PERSONA
        return get_persona_text(name)
    if features.get("keep_original_persona") and session_id:
        return get_persona_text(effective_persona_name(session_id))
    return ""


def auto_groups() -> list[str]:
    return [str(item).strip() for item in auto_config().get("auto_groups") or [] if str(item).strip()]


def bounded_days(value: int | None) -> int:
    """手动天数限制在 1 到 max_days，避免回溯过久拖垮协议端。"""
    config = basic_config()
    maximum = max(1, int(config.get("max_days") or 1))
    days = int(value if value is not None else config.get("days") or 1)
    return min(max(days, 1), maximum)
