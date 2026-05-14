"""内置实用工具插件。"""

from __future__ import annotations

from datetime import datetime
from urllib.parse import urlparse

from nonebot.matcher import Matcher
from nonebot.params import RegexGroup

from ...permission import PermLevel, PermScene
from ...plugin import Plugin
from ...utils.http import get_shared_async_client

P = Plugin("useful", display_name="实用工具", enabled=True, level=PermLevel.LOW, scene=PermScene.ALL)

ping_cmd = P.on_regex(
    r"^(?:#|/)?ping$",
    name="ping",
    display_name="Ping",
    priority=5,
    block=True,
    level=PermLevel.LOW,
    scene=PermScene.ALL,
)


@ping_cmd.handle()
async def _handle_ping(matcher: Matcher) -> None:
    """响应 ping。"""
    await matcher.finish("pong")


time_cmd = P.on_regex(
    r"^(?:#|/)?(?:时间|time)$",
    name="time",
    display_name="当前时间",
    priority=5,
    block=True,
    level=PermLevel.LOW,
    scene=PermScene.ALL,
)


@time_cmd.handle()
async def _handle_time(matcher: Matcher) -> None:
    """发送当前时间。"""
    await matcher.finish(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))


http_cmd = P.on_regex(
    r"^(?:#|/)?http\s+(\S+)$",
    name="http_status",
    display_name="HTTP 状态",
    priority=5,
    block=True,
    level=PermLevel.MEMBER,
    scene=PermScene.ALL,
)


@http_cmd.handle()
async def _handle_http(matcher: Matcher, groups: tuple = RegexGroup()) -> None:
    """查询 HTTP 状态。"""
    url = str(groups[0] if groups else "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        await matcher.finish("请输入合法的 http/https 地址")
    client = await get_shared_async_client()
    try:
        response = await client.get(url, follow_redirects=True)
    except Exception as exc:
        await matcher.finish(f"请求失败：{exc}")
    await matcher.finish(f"{response.status_code} {response.reason_phrase}")
