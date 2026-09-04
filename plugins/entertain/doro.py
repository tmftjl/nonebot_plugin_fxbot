"""Doro 结局。"""

from __future__ import annotations

from nonebot import logger
from nonebot.matcher import Matcher

from ...adapter import selfBot
from ...permission import PermLevel, PermScene
from ...plugin import Plugin
from ...utils.http import get_shared_async_client
from .config import cfg_api_urls

P = Plugin(
    "entertain",
    display_name="娱乐",
    enabled=True,
    level=PermLevel.LOW,
    scene=PermScene.ALL,
)

doro_cmd = P.on_regex(
    r"^[#＃]?(?:抽取|随机)?(?:今日)?doro结局",
    name="draw",
    display_name="doro结局",
    priority=5,
    block=True,
    level=PermLevel.LOW,
    scene=PermScene.ALL,
)


@doro_cmd.handle()
async def _handle_doro(matcher: Matcher) -> None:
    """抽取 Doro 结局。"""
    url = str(cfg_api_urls()["doro_api"]).strip()
    if not url:
        await matcher.finish("未配置 doro 接口")
    try:
        client = await get_shared_async_client()
        response = await client.get(url)
        response.raise_for_status()
        data = response.json()
    except Exception:
        logger.opt(exception=True).warning("[entertain] 获取 doro 结局失败")
        await matcher.finish("获取 doro 结局失败，请稍后重试")

    text = f"今日doro结局：\n\n{data.get('title', '')}\n\n{data.get('description', '')}\n"
    parts = [selfBot.build_segment("text", text)]
    if image := data.get("image"):
        parts.append(selfBot.build_segment("image", str(image)))
    await matcher.finish(selfBot.build_message(*parts))
