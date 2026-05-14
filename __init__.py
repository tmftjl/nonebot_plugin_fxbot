"""nonebot-plugin-fxbot 插件入口。"""

from __future__ import annotations

from nonebot import get_driver
from nonebot.plugin import PluginMetadata

from . import bootstrap

__plugin_meta__ = PluginMetadata(
    name="FxBot",
    description="面向群会员、权限控制、AI 对话和内置子插件的 NoneBot2 框架插件。",
    usage="加载插件后自动初始化配置、数据库、门禁、控制台和内置子插件。",
    type="application",
    supported_adapters={"~onebot.v11"},
)


@get_driver().on_startup
async def _fxbot_startup() -> None:
    """在 NoneBot 启动阶段初始化框架底座。"""
    await bootstrap.init()
