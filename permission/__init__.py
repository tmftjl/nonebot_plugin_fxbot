"""权限系统导出。"""

from .helpers import upsert_command_defaults, upsert_plugin_defaults
from .policy import PolicyChain
from .storage import PermissionStorage, get_storage, scan_plugins_for_permissions
from .types import (
    Decision,
    PermContext,
    PermLevel,
    PermScene,
    PolicyResult,
    perm_entry_default,
)

_CHECKER_EXPORTS = {
    "PermissionChecker",
    "permission_for",
    "permission_for_cmd",
    "permission_for_plugin",
}


def __getattr__(name: str):
    """懒加载依赖 NoneBot 的 checker 导出。"""
    if name in _CHECKER_EXPORTS:
        from . import checker

        return getattr(checker, name)
    raise AttributeError(name)


__all__ = [
    "Decision",
    "PermContext",
    "PermLevel",
    "PermScene",
    "PermissionChecker",
    "PermissionStorage",
    "PolicyChain",
    "PolicyResult",
    "get_storage",
    "permission_for",
    "permission_for_cmd",
    "permission_for_plugin",
    "perm_entry_default",
    "scan_plugins_for_permissions",
    "upsert_command_defaults",
    "upsert_plugin_defaults",
]
