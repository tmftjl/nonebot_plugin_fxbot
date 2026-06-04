"""洛克王国世界远行商人推送。"""

from __future__ import annotations

from nonebot import require
from nonebot.plugin import PluginMetadata

from ...permission import PermLevel, PermScene
from ...plugin import Plugin

require("nonebot_plugin_apscheduler")

__plugin_meta__ = PluginMetadata(
    name="远行商人推送",
    description="查询并推送洛克王国世界远行商人商品刷新。",
    usage=(
        "远行商人：查看当前远行商人信息\n"
        "订阅远行商人 [关键词...]：订阅本群商品刷新推送\n"
        "远行商人订阅：查看本群订阅\n"
        "取消订阅远行商人：取消本群订阅"
    ),
    type="application",
)

P = Plugin("rocom_merchant", display_name="远行商人", enabled=True, level=PermLevel.LOW, scene=PermScene.GROUP)

from . import commands as commands
from . import config as config
from . import scheduler as scheduler

__all__ = ["P"]
