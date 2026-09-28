"""鸣潮随机表情插件。"""

from __future__ import annotations

import re

from nonebot import logger
from nonebot.rule import Rule
from nonebot.typing import T_State
from nonebot.matcher import Matcher
from nonebot.adapters import Event

from .store import lookup_character, refresh_characters, setup_wuwa_emoji_tasks
from .client import WuwaEmojiError, CharacterEmptyError, download_image, fetch_random_image
from ...plugin import Plugin
from ...adapter import selfBot
from ...permission import PermLevel, PermScene

P = Plugin(
    "wuwa_emoji",
    display_name="鸣潮表情",
    enabled=True,
    level=PermLevel.MEMBER,
    scene=PermScene.ALL,
)

GENERIC_ALIASES = {"wuwa", "鸣潮"}
TRIGGER_PATTERN = re.compile(r"^(?:#|＃|/)?(?:来张|看看|随机)\s*(\S+)$")

_TARGET_KEY = "_wuwa_emoji_target"


def _event_text(event: Event) -> str:
    """提取事件消息文本。"""
    try:
        return str(event.get_message()).strip()
    except Exception:
        logger.opt(exception=True).debug("[wuwa_emoji] 读取消息文本失败")
        return ""


def random_emoji_rule() -> Rule:
    """构造随机鸣潮表情的动态匹配规则。"""

    async def checker(event: Event, state: T_State) -> bool:
        match = TRIGGER_PATTERN.match(_event_text(event))
        if match is None:
            return False

        name = match.group(1)
        if name.casefold() in GENERIC_ALIASES:
            state[_TARGET_KEY] = ""
            return True

        # 正式名、slug、别名都来自数据而非硬编码；未命中时静默返回 False，让行给图库等其他 matcher
        item = lookup_character(name)
        if item is None:
            return False

        state[_TARGET_KEY] = str(item["name"])
        return True

    return Rule(checker)


random_cmd = P.on_message(
    rule=random_emoji_rule(),
    block=False,
    priority=5,
    name="random_emoji",
    display_name="随机鸣潮表情",
    level=PermLevel.MEMBER,
    scene=PermScene.ALL,
)

refresh_cmd = P.on_regex(
    r"^[#＃]?(?:wuwa|鸣潮)(?:表情)?刷新$",
    name="refresh_characters",
    display_name="刷新鸣潮角色",
    priority=5,
    block=True,
    level=PermLevel.ADMIN,
    scene=PermScene.ALL,
)


@random_cmd.handle()
async def _handle_random(matcher: Matcher, state: T_State) -> None:
    """发送随机鸣潮表情。"""
    target = str(state.get(_TARGET_KEY) or "")
    try:
        image_url = await fetch_random_image(target or None)
        # 平台自己下不到这个媒体地址（QQ 官方平台由腾讯服务器代下），只能由我们下好再发字节
        image_data = image_url if selfBot.supports_media_url() else await download_image(image_url)
    except CharacterEmptyError:
        await matcher.finish(f"{target}暂时没有可用的表情")
    except WuwaEmojiError:
        logger.opt(exception=True).warning(f"[wuwa_emoji] 随机表情请求失败：target={target or '(任意角色)'}")
        await matcher.finish()
    else:
        await matcher.finish(selfBot.build_message(selfBot.build_segment("image", image_data)))


@refresh_cmd.handle()
async def _handle_refresh(matcher: Matcher) -> None:
    """刷新角色列表缓存。"""
    try:
        characters = await refresh_characters()
    except WuwaEmojiError:
        logger.opt(exception=True).warning("[wuwa_emoji] 手动刷新角色列表失败")
        await matcher.finish("角色列表刷新失败，请稍后重试")
    else:
        await matcher.finish(f"角色列表已刷新，共 {len(characters)} 个角色")


setup_wuwa_emoji_tasks()
