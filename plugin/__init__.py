"""插件包装器导出。"""

from .builder import (
    Plugin,
    get_command_display_names,
    get_plugin_display_names,
    set_command_display_name,
    set_plugin_display_name,
)

__all__ = [
    "Plugin",
    "get_command_display_names",
    "get_plugin_display_names",
    "set_command_display_name",
    "set_plugin_display_name",
]
