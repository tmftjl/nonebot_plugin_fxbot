"""群聊日报插件。"""

from __future__ import annotations

from nonebot.plugin import PluginMetadata

from ...plugin import Plugin
from ...permission import PermLevel, PermScene

__plugin_meta__ = PluginMetadata(
    name="群聊日报",
    description="回溯群历史消息，生成包含话题、群友称号、金句和质量评价的日报长图。",
    usage="/群聊分析：分析最近 24 小时\n/群聊分析 3：分析最近 3 天\n#群聊分析：同样可用",
    type="application",
)

P = Plugin(
    "group_analysis",
    display_name="群聊日报",
    enabled=True,
    level=PermLevel.LOW,
    scene=PermScene.GROUP,
)

from . import commands as commands  # noqa: E402
from .scheduler import setup_group_analysis_tasks  # noqa: E402

setup_group_analysis_tasks()

__all__ = ["P"]
