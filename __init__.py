"""nonebot-plugin-fxbot 插件入口。"""

from __future__ import annotations

from nonebot import get_driver, require
from nonebot.plugin import PluginMetadata

from . import bootstrap

require("nonebot_plugin_localstore")
require("nonebot_plugin_orm")

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


@get_driver().on_shutdown
async def _fxbot_shutdown() -> None:
    """在 NoneBot 关闭阶段释放共享资源。"""
    from .utils.http import close_shared_async_client

    await close_shared_async_client()
