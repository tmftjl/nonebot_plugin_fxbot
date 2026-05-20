"""Cultured 图库插件。"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from typing import Any

from nonebot import logger
from nonebot.adapters import Bot, Event
from nonebot.matcher import Matcher

from ...permission import PermLevel, PermScene
from ...plugin import Plugin
from ...utils.compat import build_message, build_message_segment
from ...utils.http import get_shared_async_client
from .config import face_list, load_all_commands, load_cfg, random_local_image
from . import update_gallery as update_gallery

P = Plugin("cultured", display_name="图库", enabled=True, level=PermLevel.MEMBER, scene=PermScene.ALL)


def _event_text(event: Event) -> str:
    """提取事件消息文本。"""
    try:
        return str(event.get_message()).strip()
    except Exception:
        return ""


def _command_names() -> list[str]:
    """读取所有 API 图库命令名。"""
    names: list[str] = []
    for item in load_all_commands():
        name = str(item["name"]).strip()
        if name:
            names.append(name)
    return names


def _build_api_regex() -> re.Pattern[str] | None:
    """构造 API 图库命令匹配表达式。"""
    names = _command_names()
    if not names:
        return None
    joined = "|".join(re.escape(name) for name in names)
    return re.compile(rf"^(?:#|/)?(?:来张|看看|随机)\s*({joined})$", re.I)


def _build_picture(bot: Bot, url: str, response_text: str | None = None):
    """构造图片回复消息。"""
    parts: list[Any] = []
    if response_text:
        parts.append(build_message_segment(bot, "text", response_text))
    parts.append(build_message_segment(bot, "image", url))
    return build_message(bot, *parts)


def _create_api_handler(bot: Bot, command: dict[str, Any]) -> Callable[[], Any]:
    """根据配置创建 API 图库处理器。"""
    url = str(command["url"]).strip()
    method = str(command["method"]).strip().lower()
    response_text = command["response_text"]
    regex_pattern = str(command["regex"])

    async def _fetch_text() -> str:
        client = await get_shared_async_client()
        response = await client.get(url)
        response.raise_for_status()
        return response.text.replace("\\", "/").strip()

    if method == "direct":
        return lambda: _build_picture(bot, url, response_text)

    if method == "get_text":

        async def _handle_get_text():
            return _build_picture(bot, await _fetch_text(), response_text)

        return _handle_get_text

    if method == "get_text_regex":

        async def _handle_get_text_regex():
            match = re.search(regex_pattern, await _fetch_text())
            if not match:
                return None
            return _build_picture(bot, match.group(0), response_text)

        return _handle_get_text_regex

    logger.warning(f"[Cultured] 未知图库获取模式: {method}")
    return lambda: None


def _api_handlers(bot: Bot) -> list[tuple[str, Callable[[], Any]]]:
    """加载 API 图库处理器。"""
    handlers: list[tuple[str, Callable[[], Any]]] = []
    for command in load_all_commands():
        name = str(command["name"]).strip()
        if name:
            handlers.append((name, _create_api_handler(bot, command)))
    return handlers


def _pick_face_image(name: str, bot: Bot):
    """优先使用本地图库，缺失时退回备用 API。"""
    path = random_local_image(name)
    if path is not None:
        return build_message_segment(bot, "image", path)
    fallback = str(load_cfg()["fallback_api"])
    return build_message_segment(bot, "image", fallback.format(name=name))


api_cmd = P.on_regex(
    r"^(?:#|/)?(?:来张|看看|随机).+",
    name="pictures_api",
    display_name="看看腿",
    priority=5,
    block=True,
    level=PermLevel.MEMBER,
    scene=PermScene.ALL,
)

list_cmd = P.on_regex(
    r"^#?(?:cultured|表情包)列表",
    name="pictures_list",
    display_name="表情包列表",
    priority=5,
    block=True,
    level=PermLevel.MEMBER,
    scene=PermScene.ALL,
)

picture_cmd = P.on_regex(
    r"^(?:#|/)?(?:来张|看看|随机)\s*(\S+)",
    name="pictures_local",
    display_name="随机本地表情",
    priority=5,
    block=True,
    level=PermLevel.MEMBER,
    scene=PermScene.ALL,
)


@list_cmd.handle()
async def _handle_list(matcher: Matcher) -> None:
    """发送图库列表。"""
    faces = face_list()
    await matcher.finish("表情列表：\n" + ("、".join(faces) or "(空)") + "\n\n使用 #随机<名称>")


@api_cmd.handle()
async def _handle_api_picture(matcher: Matcher, bot: Bot, event: Event) -> None:
    """发送 API 图库图片。"""
    if not bool(load_cfg()["random_picture_open"]):
        await matcher.finish()

    text = _event_text(event)
    api_regex = _build_api_regex()
    if api_regex is not None:
        match = api_regex.match(text)
        if match:
            target = match.group(1)
            for name, handler in _api_handlers(bot):
                if name.lower() == target.lower():
                    try:
                        result = handler()
                        if asyncio.iscoroutine(result):
                            result = await result
                    except Exception:
                        logger.opt(exception=True).warning(f"[Cultured] API 图库 {name} 请求失败")
                        await matcher.finish("图库接口请求失败")
                    if result:
                        await matcher.finish(result)
                    await matcher.finish()
    await matcher.finish()


@picture_cmd.handle()
async def _handle_picture(matcher: Matcher, bot: Bot, event: Event) -> None:
    """发送随机本地图库图片。"""
    if not bool(load_cfg()["random_picture_open"]):
        return

    text = _event_text(event)
    match = re.match(r"^(?:#|/)?(?:来张|看看|随机)\s*(\S+)", text)
    name = match.group(1) if match else ""
    if name in face_list():
        await matcher.finish(build_message(bot, _pick_face_image(name, bot)))
