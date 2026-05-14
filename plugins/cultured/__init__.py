"""Cultured 图库插件基础入口。"""

from __future__ import annotations

from nonebot.matcher import Matcher
from nonebot.params import RegexGroup

from ...permission import PermLevel, PermScene
from ...plugin import Plugin

P = Plugin("cultured", display_name="图库", enabled=True, level=PermLevel.MEMBER, scene=PermScene.ALL)

list_cmd = P.on_regex(
    r"^#?(?:cultured|图库|表情包)列表$",
    name="pictures_list",
    display_name="图库列表",
    priority=5,
    block=True,
    level=PermLevel.MEMBER,
    scene=PermScene.ALL,
)

picture_cmd = P.on_regex(
    r"^(?:#|/)?(?:来张|看看|随机)\s*(\S+)$",
    name="pictures_random",
    display_name="随机图片",
    priority=5,
    block=True,
    level=PermLevel.MEMBER,
    scene=PermScene.ALL,
)


@list_cmd.handle()
async def _handle_list(matcher: Matcher) -> None:
    """提示图库功能状态。"""
    await matcher.finish("图库功能已预留，图片源配置将在后续版本接入。")


@picture_cmd.handle()
async def _handle_picture(matcher: Matcher, groups: tuple = RegexGroup()) -> None:
    """提示随机图片功能状态。"""
    name = str(groups[0] if groups else "").strip()
    await matcher.finish(f"图库「{name}」暂未配置图片源。")
