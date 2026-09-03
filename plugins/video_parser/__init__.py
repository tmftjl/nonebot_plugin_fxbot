"""视频解析内置插件。"""

from __future__ import annotations

from nonebot.plugin import PluginMetadata

from ...permission import PermLevel, PermScene
from ...plugin import Plugin

__plugin_meta__ = PluginMetadata(
    name="视频解析",
    description="解析抖音、快手、微博、小红书、B站视频链接并发送封面与视频。",
    usage=(
        "发送支持平台的视频链接自动解析。\n"
        "#开启解析 / #关闭解析：控制本群解析\n"
        "#全局开启解析 / #全局关闭解析：控制全局解析\n"
        "#B站登录：扫码保存 B 站登录凭据"
    ),
    type="application",
)

P = Plugin(
    "video_parser",
    display_name="视频解析",
    enabled=True,
    level=PermLevel.LOW,
    scene=PermScene.ALL,
)

from . import config as config
from . import matcher as matcher

__all__ = ["P"]
