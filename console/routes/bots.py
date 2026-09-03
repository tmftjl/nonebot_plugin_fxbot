"""在线 Bot 状态路由。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from nonebot import get_bots

from ..auth import bearer_auth

router = APIRouter(prefix="/bots", tags=["fxbot-bots"], dependencies=[Depends(bearer_auth)])


@router.get("")
async def list_bots() -> dict[str, list[dict[str, str]]]:
    """列出在线 Bot。"""
    bots = []
    for bot_id, bot in get_bots().items():
        bots.append({"self_id": str(bot_id), "adapter": bot.adapter.get_name()})
    return {"bots": bots}
