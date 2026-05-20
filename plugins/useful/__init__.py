"""内置实用工具插件。"""

from __future__ import annotations

import base64
from typing import Any
from urllib.parse import urlencode

import httpx
from nonebot.adapters import Event
from nonebot.matcher import Matcher
from nonebot.params import RegexGroup

from ...permission import PermLevel, PermScene
from ...plugin import Plugin
from ...utils.http import get_shared_async_client
from .config import cfg_taffy
from . import cos_upload as cos_upload
from . import waves_analyze as waves_analyze

P = Plugin("useful", display_name="实用工具", enabled=True, level=PermLevel.LOW, scene=PermScene.ALL)


def _fmt_bytes(value: Any) -> str:
    """格式化字节数。"""
    try:
        number = int(value or 0)
    except Exception:
        return str(value)
    if number < 1024:
        return f"{number} B"
    units = ["KiB", "MiB", "GiB", "TiB", "PiB", "EiB"]
    size = float(number)
    unit_index = -1
    while size >= 1024 and unit_index < len(units) - 1:
        size /= 1024
        unit_index += 1
    return f"{size:.2f} {units[unit_index]}"


taffy_cmd = P.on_regex(
    r"^#?查询流量\s*(.*)",
    name="query",
    display_name="查询流量",
    priority=5,
    block=True,
    level=PermLevel.MEMBER,
    scene=PermScene.ALL,
)


@taffy_cmd.handle()
async def _handle_taffy(matcher: Matcher, groups: tuple = RegexGroup()) -> None:
    """查询 Taffy 流量统计。"""
    cfg = cfg_taffy()
    api_url = str(cfg.get("api_url") or "").strip()
    if not api_url:
        await matcher.finish("未配置 Taffy API 地址")

    query_user = str(groups[0] if groups else "").strip()
    url = api_url
    if query_user:
        url += ("&" if "?" in url else "?") + urlencode({"user": query_user})

    headers = {"User-Agent": "NoneBot FxBot Useful"}
    username = str(cfg.get("username") or "").strip()
    password = str(cfg.get("password") or "").strip()
    if username and password:
        token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
        headers["Authorization"] = f"Basic {token}"

    try:
        client = await get_shared_async_client()
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()
    except httpx.HTTPError as exc:
        await matcher.finish(f"请求 API 失败：{exc}")
    except Exception:
        await matcher.finish("查询失败：无法连接到服务器或数据格式异常")

    lines = ["--- 代理服务状态 ---"]
    if query_user:
        user = data.get("single_user") or {}
        if bool(data.get("found")) and isinstance(user, dict):
            up = user.get("upstream_bytes")
            down = user.get("downstream_bytes")
            lines.append(f"[用户名: {user.get('username', '')}]")
            lines.append(f"已请求次数: {user.get('total_requests', 0)}")
            lines.append(f"上行流量: {_fmt_bytes(up)}")
            lines.append(f"下行流量: {_fmt_bytes(down)}")
            lines.append(f"累计流量: {_fmt_bytes((up or 0) + (down or 0))}")
        else:
            lines.append(f"未找到用户[{query_user}] 的统计信息")
    else:
        lines.append("[所有用户]")
        users = data.get("all_users") or []
        if isinstance(users, list) and users:
            for user in users:
                if isinstance(user, dict):
                    up = user.get("upstream_bytes")
                    down = user.get("downstream_bytes")
                    lines.append(f"[{user.get('username', '')}] 流量 {_fmt_bytes((up or 0) + (down or 0))}")
        else:
            lines.append("无用户数据")

    lines.extend(
        [
            "",
            "--- 全局状态 ---",
            f"服务启动时长: {data.get('global_uptime', '')}",
            f"去广告 API 调用: {data.get('global_dmdaili_api_calls', '')}",
            f"当前代理 IP: {data.get('cached_proxy_ip', '')}",
            f"缓存剩余时长: {data.get('cache_expires_in', '')}",
            f"当前黑名单 IP 数: {data.get('current_blacklist_size', '')}",
            f"累计拉黑 IP 数: {data.get('global_blacklist_events', '')}",
        ]
    )
    await matcher.finish("\n".join(lines))


panel_upload_cmd = P.on_regex(
    r"^ww上传.*面板图",
    name="upload",
    display_name="上传面板图提示",
    priority=5,
    block=True,
    level=PermLevel.LOW,
    scene=PermScene.ALL,
)

panel_list_cmd = P.on_regex(
    r"^ww.*面板图列表",
    name="list",
    display_name="面板图列表提示",
    priority=5,
    block=True,
    level=PermLevel.LOW,
    scene=PermScene.ALL,
)

panel_refresh_cmd = P.on_regex(
    r"^ww(?:刷新|更新)?面板(?:刷新)?",
    name="refresh",
    display_name="刷新面板提示",
    priority=5,
    block=True,
    level=PermLevel.LOW,
    scene=PermScene.ALL,
)


@panel_upload_cmd.handle()
async def _handle_panel_upload(matcher: Matcher, event: Event) -> None:
    """发送面板图上传提示。"""
    group_id = str(getattr(event, "group_id", "") or "")
    if group_id != "757463664":
        await matcher.finish("上传面板图需加群 757463664 审核")
    await matcher.finish()


@panel_list_cmd.handle()
async def _handle_panel_list(matcher: Matcher, event: Event) -> None:
    """发送面板图列表提示。"""
    if getattr(event, "group_id", None) is not None:
        await matcher.finish("为防止刷屏，面板图仅支持私聊查看")
    await matcher.finish()


@panel_refresh_cmd.handle()
async def _handle_panel_refresh(matcher: Matcher) -> None:
    """发送面板刷新提示。"""
    await matcher.finish("更新已完成，下次刷单个角色请使用 `ww刷新【角色名】面板`")
