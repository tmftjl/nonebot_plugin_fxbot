"""NoneBot 依赖注入入口。"""

from __future__ import annotations

from typing import Annotated

from nonebot.adapters import Bot, Event
from nonebot.params import Depends

from . import cache
from .fetch import build_session
from .interface import Interface, get_interface
from .model import Session


async def get_session(bot: Bot, event: Event) -> Session:
    """返回当前事件的统一会话信息。"""
    session_id = None
    try:
        session_id = event.get_session_id()
    except ValueError:
        session_id = None
    if session_id is not None:
        if session := cache.get_session(bot, session_id):
            return session

    session = await build_session(bot, event)
    if session_id is not None:
        cache.save_session(bot, session_id, session)
    return session


def UniSession() -> Session:
    return Depends(get_session)


Uninfo = Annotated[Session, UniSession()]


def QueryInterface() -> Interface:
    return Depends(get_interface)


QryItrface = Annotated[Interface, QueryInterface()]
