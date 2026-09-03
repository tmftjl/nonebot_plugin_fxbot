"""洛克王国资料查询与远行商人推送。"""

from __future__ import annotations

from nonebot.plugin import PluginMetadata

from ...permission import PermLevel, PermScene
from ...plugin import Plugin

__plugin_meta__ = PluginMetadata(
    name="洛克王国",
    description="洛克王国资料查询、配种查询、精灵蛋反查与远行商人推送。",
    usage=(
        "#远行商人：查看当前远行商人信息\n"
        "#开启远行商人：订阅本群/私聊商品刷新推送\n"
        "#关闭远行商人：取消本群/私聊订阅\n"
        "#远行商人订阅：查看当前会话订阅状态\n"
        "#图鉴 喵喵：查看精灵图鉴\n"
        "#技能信息 抓挠：查看技能资料\n"
        "#配种 喵喵 火花：查询是否可配种\n"
        "#查蛋 0.23 1.30：按尺寸重量反查精灵蛋\n"
        "#查找精灵 属性:火 速度:>100：按条件查找精灵\n"
        "#属性克制：查看属性克制表\n"
        "#洛克下载资源：手动检查并下载运行时资源"
    ),
    type="application",
)

P = Plugin(
    "rocom",
    display_name="洛克王国",
    enabled=True,
    level=PermLevel.LOW,
    scene=PermScene.ALL,
)

from . import commands_info as commands_info
from . import commands_merchant as commands_merchant
from . import config as config
from . import resource_downloader as resource_downloader
from .scheduler import setup_rocom_merchant_tasks

setup_rocom_merchant_tasks()

__all__ = ["P"]
