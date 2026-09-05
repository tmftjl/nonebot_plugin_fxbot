"""发病语录。"""

from __future__ import annotations

from nonebot.matcher import Matcher

from ...adapter import selfBot
from ...adapter import Uninfo
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

sick_cmd = P.on_regex(
    r"^(?:#|＃|/)?发病语录",
    name="get",
    display_name="发病语录",
    priority=5,
    block=True,
    level=PermLevel.LOW,
    scene=PermScene.ALL,
)


@sick_cmd.handle()
async def _handle_sick(matcher: Matcher, session: Uninfo) -> None:
    """获取发病语录。"""
    url = str(cfg_api_urls()["sick_quote_api"]).strip()
    if not url:
        await matcher.finish("未配置发病语录接口")
    try:
        client = await get_shared_async_client()
        response = await client.get(url)
        response.raise_for_status()
        data = response.json()
    except Exception:
        await matcher.finish("获取发病语录失败，请稍后重试")

    text = str(data.get("message") or data.get("msg") or "")
    user_id = session.user.id
    await matcher.finish(
        selfBot.build_message(
            selfBot.build_segment("at", user_id) if user_id else None,
            selfBot.build_segment("text", f"\n{text}" if user_id else text),
        )
    )
