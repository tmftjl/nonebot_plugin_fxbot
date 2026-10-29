"""聊天存储与学习的配置读取；每次使用都重新读取，保持热更新。"""

from __future__ import annotations

from typing import Any

from .models import ChatSessionState
from ...config import get_manager as get_config_manager

JARGON_DEFAULTS: dict[str, Any] = {
    "learning_list": [],
    "groups": [],
}
EXPRESSION_DEFAULTS: dict[str, Any] = {
    "checked_only": False,
    "self_reflect": True,
    "max_expression_learner": 3,
    "learning_list": [],
    "groups": [],
}
VECTOR_DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "index_path": "data/expression_selection/expression_vector_index.json",
    "candidate_pool_size": 50,
}


def tenant_learn_enabled(state: ChatSessionState | None) -> bool:
    """本租户显式开关优先；未设置时按开启处理，对齐 MaiBot 的默认学习范围。"""
    return True if state is None or state.learn_enabled is None else bool(state.learn_enabled)


def jargon_config() -> dict[str, Any]:
    """返回带默认值的黑话注入配置。"""
    try:
        value = get_config_manager().get_system()["chat"].get("jargon")
    except Exception:
        value = None
    merged = dict(JARGON_DEFAULTS)
    if isinstance(value, dict):
        merged.update(value)
    return merged


def expression_config() -> dict[str, Any]:
    """返回带默认值的表达学习与注入配置。"""
    try:
        value = get_config_manager().get_system()["chat"].get("expression")
    except Exception:
        value = None
    merged = dict(EXPRESSION_DEFAULTS)
    if isinstance(value, dict):
        merged.update(value)
    return merged


def expression_vector_config() -> dict[str, Any]:
    """返回带默认值的表达向量配置；嵌套对象单独合并。"""
    merged = dict(VECTOR_DEFAULTS)
    value = expression_config().get("vector")
    if isinstance(value, dict):
        merged.update(value)
    return merged
