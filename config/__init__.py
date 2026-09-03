"""运行时配置辅助接口。"""

from .manager import ConfigManager, get_manager
from .proxy import ConfigProxy, deep_merge
from .system_defaults import SYSTEM_DEFAULTS

__all__ = [
    "ConfigManager",
    "ConfigProxy",
    "SYSTEM_DEFAULTS",
    "deep_merge",
    "get_manager",
]
