"""主运行时事件循环桥接工具（原样取自 MaiBot ``src/common/runtime_loop.py``）。"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Optional, TypeVar

T = TypeVar("T")

_main_loop: Optional[asyncio.AbstractEventLoop] = None


def set_main_loop(loop: Optional[asyncio.AbstractEventLoop]) -> None:
    global _main_loop
    _main_loop = loop


def get_main_loop() -> Optional[asyncio.AbstractEventLoop]:
    return _main_loop


async def run_on_main_loop(coro: Awaitable[T]) -> T:
    """在主运行时事件循环执行协程；已在主循环时直接 await。"""
    loop = get_main_loop()
    current_loop = asyncio.get_running_loop()
    if loop is None or loop.is_closed() or loop is current_loop:
        return await coro

    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return await asyncio.wrap_future(future)
