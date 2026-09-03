"""内置实用工具插件。"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from nonebot.matcher import Matcher

from ...permission import PermLevel, PermScene
from ...plugin import Plugin
from . import cos_upload as cos_upload
from . import dps_chart as dps_chart
from . import waves_analyze as waves_analyze

P = Plugin(
    "useful",
    display_name="实用工具",
    enabled=True,
    level=PermLevel.LOW,
    scene=PermScene.ALL,
)


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


panel_refresh_cmd = P.on_regex(
    r"^ww(?:刷新|更新)?面板(?:刷新)?",
    name="refresh",
    display_name="刷新面板提示",
    priority=5,
    block=True,
    level=PermLevel.LOW,
    scene=PermScene.ALL,
)


@panel_refresh_cmd.handle()
async def _handle_panel_refresh(matcher: Matcher) -> None:
    """发送面板刷新提示。"""
    await matcher.finish("更新已完成，下次刷单个角色请使用 `ww刷新【角色名】面板`")
