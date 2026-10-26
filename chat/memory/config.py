"""长期记忆的配置读取；每次使用都重新读取，保持热更新。"""

from __future__ import annotations

from typing import Any

from .models import ChatSessionState
from ...config import get_manager as get_config_manager

DEFAULTS: dict[str, Any] = {
    "learn_enabled": False,
    "extract_interval_seconds": 300,
    "max_tenants_per_tick": 3,
    "algorithm_version": 1,
    "episode_idle_minutes": 20,
    "episode_max_messages": 80,
    "episode_max_hours": 2,
    "episode_min_messages": 10,
    "profile_fact_threshold": 20,
    "max_input_chars_per_batch": 6000,
    "inject_profile_chars": 300,
    "inject_facts_chars": 400,
    "inject_episodes_chars": 300,
    "inject_total_chars": 1500,
}


def memory_config() -> dict[str, Any]:
    """返回带默认值的记忆配置。"""
    try:
        value = get_config_manager().get_system()["chat"].get("memory")
    except Exception:
        value = None
    merged = dict(DEFAULTS)
    if isinstance(value, dict):
        merged.update(value)
    return merged


def tenant_learn_enabled(state: ChatSessionState | None) -> bool:
    """本租户显式开关优先，否则跟随全局。"""
    if state is not None and state.learn_enabled is not None:
        return bool(state.learn_enabled)
    return bool(memory_config()["learn_enabled"])
