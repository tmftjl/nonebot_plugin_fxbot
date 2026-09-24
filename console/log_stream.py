"""进程内日志环形缓冲与订阅分发。

模块导入即安装 loguru 处理器，不依赖启动钩子：日志要从进程早期就开始收集，
而 NoneBot 的 lifespan 启动钩子列表在启动前已快照，启动阶段才注册的钩子不会执行。
"""

from __future__ import annotations

import sys
import secrets
import threading
import traceback
from typing import TYPE_CHECKING, Any
from asyncio import Queue, QueueFull, QueueEmpty, get_running_loop
from collections import deque

from nonebot import logger
from nonebot.log import default_filter

from ..utils.tz import as_shanghai

if TYPE_CHECKING:
    # loguru 只在 __init__.pyi 里声明 Message，运行时顶层仅导出 logger，只能作类型注解用
    from asyncio import AbstractEventLoop

    from loguru import Message

_BUFFER_SIZE = 2000
_QUEUE_SIZE = 1000
_TIME_FORMAT = "%m-%d %H:%M:%S"
# 进程标识：seq 每次重启都从 1 重新计数，前端靠它区分「重连补发」和「服务端已重启」
_BOOT_ID = secrets.token_hex(4)

_lock = threading.Lock()
_records: deque[dict[str, Any]] = deque(maxlen=_BUFFER_SIZE)
_subscribers: set[Queue] = set()
_seq = 0
_loop: AbstractEventLoop | None = None


def _dispatch(item: dict[str, Any]) -> None:
    """把一条记录投递给所有订阅队列，必须在事件循环线程执行。"""
    for queue in _subscribers:
        try:
            queue.put_nowait(item)
        except QueueFull:
            # 队列满说明该客户端消费不过来，丢最旧的一条保住「最新可见」优先
            try:
                queue.get_nowait()
                queue.put_nowait(item)
            except (QueueEmpty, QueueFull):
                pass


def _sink(message: Message) -> None:
    """loguru 处理器：写入环形缓冲并分发给订阅者。"""
    try:
        record = message.record
        global _seq
        with _lock:
            _seq += 1
            item = {
                "boot": _BOOT_ID,
                "seq": _seq,
                "time": as_shanghai(record["time"]).strftime(_TIME_FORMAT),
                "level": record["level"].name,
                "name": record["name"] or "",
                "text": str(message).rstrip("\n"),
            }
            _records.append(item)
            loop = _loop
            has_subscriber = bool(_subscribers)
        if loop is not None and has_subscriber:
            loop.call_soon_threadsafe(_dispatch, item)
    except Exception:
        # 不能再用 logger 报错：本处理器就是日志入口，报错会递归回自己，直接写 stderr。
        # 也不能静默吞掉：这条链路本身就是控制台排查用的，悄悄丢记录等于让页面「看起来正常」。
        traceback.print_exc(file=sys.stderr)


def boot_id() -> str:
    """返回本进程的日志缓冲标识。"""
    return _BOOT_ID


def snapshot(after_seq: int | None = None) -> list[dict[str, Any]]:
    """取出缓冲记录，给定 seq 时只返回其后的部分。"""
    with _lock:
        if after_seq is None:
            return list(_records)
        return [item for item in _records if item["seq"] > after_seq]


def subscribe() -> Queue:
    """注册一个订阅队列，并记下事件循环供跨线程投递使用。"""
    global _loop
    queue: Queue = Queue(maxsize=_QUEUE_SIZE)
    _loop = get_running_loop()
    with _lock:
        _subscribers.add(queue)
    return queue


def unsubscribe(queue: Queue) -> None:
    """注销订阅队列。"""
    with _lock:
        _subscribers.discard(queue)


def _install() -> None:
    """安装 loguru 处理器。

    复用 NoneBot 自带的过滤器，级别门槛由 .env 的 LOG_LEVEL 统一控制，控制台与终端一致。
    格式只要 ``{message}``：loguru 会自动追加 ``\\n{exception}``，异常堆栈自带进同一段文本；
    而 NoneBot 的 default_format 是给终端看的带颜色标记单行串，级别和模块名挤在字符串里，
    前端无法分别染色，所以级别、模块名从 record 的结构化字段取。
    """
    logger.add(
        _sink,
        level=0,
        filter=default_filter,
        format="{message}",
        colorize=False,
        backtrace=False,
        diagnose=False,
    )


_install()
