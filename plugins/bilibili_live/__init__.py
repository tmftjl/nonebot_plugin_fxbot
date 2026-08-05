"""B 站直播订阅与开播推送。"""

from __future__ import annotations

from nonebot.plugin import PluginMetadata

from ...permission import PermLevel, PermScene
from ...plugin import Plugin

__plugin_meta__ = PluginMetadata(
    name="B站直播推送",
    description="订阅 B 站直播间，在主播开播时向当前会话推送通知。",
    usage=(
        "#B站直播订阅 直播间号/链接：订阅当前会话\n"
        "#B站直播订阅uid123456：按主播 UID 订阅当前会话\n"
        "#B站直播取消 直播间号/链接：取消订阅\n"
        "#B站直播列表：查看当前会话的订阅\n"
        "#B站直播查询 直播间号/链接：查询当前状态"
    ),
    type="application",
)

P = Plugin(
    "bilibili_live",
    display_name="B站直播推送",
    enabled=True,
    level=PermLevel.LOW,
    scene=PermScene.ALL,
)

from . import commands as commands
from .scheduler import setup_bilibili_live_tasks

setup_bilibili_live_tasks()

__all__ = ["P"]
