"""学习目标匹配与共享租户解析。

对齐 MaiBot（commit 062d45c，`src/common/utils/utils_config.py`）的 `learning_list`
与 `expression_groups` / `jargon_groups`，按 FxBot 的 `tenant = session_id` 改写。
"""

from __future__ import annotations

from typing import Any
from dataclasses import dataclass

from ..session_hooks import session_policy, can_share_sessions

GROUP = "group"
PRIVATE = "private"


@dataclass(frozen=True)
class TenantTarget:
    """一个租户对应的平台、聊天类型与目标 ID。"""

    platform: str
    item_id: str
    is_group: bool
    namespace: str


def tenant_target(tenant: str) -> TenantTarget | None:
    """从 `平台:Bot:group|private:ID` 解析目标；格式不符返回 None。"""
    text = str(tenant or "")
    if text.count(":") < 2:
        return None
    namespace, kind, item_id = text.rsplit(":", 2)
    if kind not in {GROUP, PRIVATE} or not item_id:
        return None
    return TenantTarget(
        platform=namespace.split(":", 1)[0],
        item_id=item_id,
        is_group=kind == GROUP,
        namespace=namespace,
    )


def _values(item: Any) -> tuple[str, str, bool | None]:
    """读取一条配置目标；`*` 由调用方决定是否按留空处理。"""
    if not isinstance(item, dict):
        return "", "", None
    platform = str(item.get("platform") or "").strip()
    item_id = str(item.get("item_id") or "").strip()
    is_group = {GROUP: True, PRIVATE: False}.get(str(item.get("type") or "").strip().lower())
    return platform, item_id, is_group


def _learning_values(item: Any) -> tuple[str, str, bool | None]:
    """learning_list 里的 `*` 与留空等价，都表示该层级的默认值。"""
    platform, item_id, is_group = _values(item)
    return ("" if platform == "*" else platform, "" if item_id == "*" else item_id, is_group)


def find_learning_item(items: Any, target: TenantTarget | None) -> dict[str, Any] | None:
    """取最具体的一条规则：指定聊天 > 平台默认 > 全局默认。"""
    entries = [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []
    if target is None or not entries:
        return None
    any_platform = platform_default = None
    for item in entries:
        platform, item_id, is_group = _learning_values(item)
        if is_group is not None and is_group != target.is_group:
            continue
        if item_id:
            if item_id != target.item_id:
                continue
            if platform == target.platform:
                return item
            if not platform and any_platform is None:
                any_platform = item
        elif platform and platform == target.platform and platform_default is None:
            platform_default = item
    if any_platform is not None:
        return any_platform
    if platform_default is not None:
        return platform_default
    fallback = None
    for item in entries:
        platform, item_id, is_group = _learning_values(item)
        if platform or item_id:
            continue
        if is_group is target.is_group:
            return item
        if fallback is None:
            fallback = item
    return fallback


def learning_flags(items: Any, target: TenantTarget | None, *, default: bool = True) -> tuple[bool, bool]:
    """返回当前聊天的 (use, learn)；没有命中规则时用默认值。"""
    item = find_learning_item(items, target)
    if item is None:
        return default, default
    return bool(item.get("use", True)), bool(item.get("learn", True))


def target_matches(item: Any, target: TenantTarget) -> bool:
    """共享组目标是否覆盖当前聊天；目标必须写全平台与 ID，允许 `*` 通配。"""
    platform, item_id, is_group = _values(item)
    if not platform or not item_id:
        return False
    if is_group is not None and is_group != target.is_group:
        return False
    if platform != "*" and platform != target.platform:
        return False
    return item_id in {"*", target.item_id}


def share_targets(groups: Any, target: TenantTarget | None) -> list[dict[str, Any]]:
    """当前聊天所在共享组里的全部目标。"""
    if target is None or not isinstance(groups, list):
        return []
    shared: list[dict[str, Any]] = []
    for group in groups:
        targets = group.get("targets") if isinstance(group, dict) else None
        if not isinstance(targets, list):
            continue
        if any(target_matches(item, target) for item in targets):
            shared.extend(item for item in targets if isinstance(item, dict))
    return shared


def share_tenants(
    current: str, target: TenantTarget | None, targets: list[dict[str, Any]], known: list[str]
) -> list[str]:
    """把共享组目标解析成已知租户；通配目标按平台与聊天类型筛选。"""
    result = [current]
    if target is None or not session_policy(current).allow_sharing:
        return result
    for item in targets:
        platform, item_id, is_group = _values(item)
        if not platform or not item_id or is_group is None:
            continue
        for tenant in known:
            if not can_share_sessions(current, tenant):
                continue
            other = tenant_target(tenant)
            if other is None or other.is_group != is_group:
                continue
            if platform not in {"", "*"} and platform != other.platform:
                continue
            if item_id != "*" and item_id != other.item_id:
                continue
            if tenant not in result:
                result.append(tenant)
    return result
