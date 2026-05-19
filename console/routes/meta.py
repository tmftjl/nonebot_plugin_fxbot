"""控制台元信息路由。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from ...plugin.builder import get_command_display_names, get_plugin_display_names
from ..auth import bearer_auth

router = APIRouter(tags=["fxbot-meta"], dependencies=[Depends(bearer_auth)])


@router.get("/plugins")
async def get_plugins() -> dict[str, str]:
    """获取插件展示名。"""
    return get_plugin_display_names()


@router.get("/commands")
async def get_commands() -> dict[str, dict[str, str]]:
    """获取命令展示名。"""
    return get_command_display_names()


@router.get("/stats/today")
async def get_stats_today() -> dict[str, Any]:
    """返回消息统计占位数据。"""
    return {"bots": {}}
