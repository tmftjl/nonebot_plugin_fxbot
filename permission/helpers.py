"""权限默认配置写入辅助函数。"""

from __future__ import annotations

from typing import Any

from .storage import get_storage
from .types import perm_entry_default


def _as_str_list(value: Any) -> list[str]:
    """转换为字符串列表。"""
    if value is None:
        return []
    if not isinstance(value, (list, tuple, set)):
        raise TypeError("应为列表类型")
    return [str(item) for item in value if item is not None]


def _update_entry(
    entry: dict[str, Any],
    *,
    enabled: bool | None = None,
    level: str | None = None,
    scene: str | None = None,
    wl_users: list[str] | None = None,
    wl_groups: list[str] | None = None,
    bl_users: list[str] | None = None,
    bl_groups: list[str] | None = None,
) -> None:
    """按传入参数更新权限条目。"""
    if enabled is not None:
        entry["enabled"] = bool(enabled)
    if level is not None:
        entry["level"] = level
    if scene is not None:
        entry["scene"] = scene
    if wl_users is not None:
        entry.setdefault("whitelist", {})["users"] = _as_str_list(wl_users)
    if wl_groups is not None:
        entry.setdefault("whitelist", {})["groups"] = _as_str_list(wl_groups)
    if bl_users is not None:
        entry.setdefault("blacklist", {})["users"] = _as_str_list(bl_users)
    if bl_groups is not None:
        entry.setdefault("blacklist", {})["groups"] = _as_str_list(bl_groups)


def upsert_plugin_defaults(
    plugin: str,
    *,
    enabled: bool | None = None,
    level: str | None = None,
    scene: str | None = None,
    wl_users: list[str] | None = None,
    wl_groups: list[str] | None = None,
    bl_users: list[str] | None = None,
    bl_groups: list[str] | None = None,
) -> None:
    """写入子插件默认权限配置。"""
    storage = get_storage()
    data = storage.load()
    sub_plugins = data.setdefault("sub_plugins", {})
    plugin_node = sub_plugins.setdefault(plugin, {})
    entry = plugin_node.setdefault("top", perm_entry_default())
    plugin_node.setdefault("commands", {})
    _update_entry(
        entry,
        enabled=enabled,
        level=level,
        scene=scene,
        wl_users=wl_users,
        wl_groups=wl_groups,
        bl_users=bl_users,
        bl_groups=bl_groups,
    )
    storage.mark_dirty(data)


def upsert_command_defaults(
    plugin: str,
    command: str,
    *,
    enabled: bool | None = None,
    level: str | None = None,
    scene: str | None = None,
    wl_users: list[str] | None = None,
    wl_groups: list[str] | None = None,
    bl_users: list[str] | None = None,
    bl_groups: list[str] | None = None,
) -> None:
    """写入子插件命令默认权限配置。"""
    storage = get_storage()
    data = storage.load()
    sub_plugins = data.setdefault("sub_plugins", {})
    plugin_node = sub_plugins.setdefault(plugin, {})
    plugin_node.setdefault("top", perm_entry_default())
    commands = plugin_node.setdefault("commands", {})
    entry = commands.setdefault(command, perm_entry_default())
    _update_entry(
        entry,
        enabled=enabled,
        level=level,
        scene=scene,
        wl_users=wl_users,
        wl_groups=wl_groups,
        bl_users=bl_users,
        bl_groups=bl_groups,
    )
    storage.mark_dirty(data)
