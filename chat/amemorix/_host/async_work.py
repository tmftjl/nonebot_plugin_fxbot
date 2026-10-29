"""同步工作边界：让出事件循环，同时保持调用方的锁和资源生命周期。

原样取自 MaiBot ``src/common/async_work.py``，日志改走宿主日志。
"""

from __future__ import annotations

import asyncio
from typing import Callable, ParamSpec, TypeVar

from .logger import get_logger

_P = ParamSpec("_P")
_T = TypeVar("_T")
logger = get_logger("A_Memorix.async_work")


async def run_sync_work(function: Callable[_P, _T], *args: _P.args, **kwargs: _P.kwargs) -> _T:
    """在线程中完成一个同步步骤；取消时也等线程退出后再释放调用方持有的锁。"""
    work = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(work)
    except asyncio.CancelledError:
        while not work.done():
            try:
                await asyncio.shield(work)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        try:
            work.result()
        except Exception:
            logger.exception("取消期间同步工作执行失败")
        raise
