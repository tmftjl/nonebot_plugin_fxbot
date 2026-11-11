"""可选后台审计队列，与需要即时执行的生命周期通知分离。"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any
from weakref import WeakSet
from contextvars import Context
from collections.abc import Mapping, Callable

from nonebot import logger

if TYPE_CHECKING:
    from .hooks import ChatHookContext

_observers: WeakSet[AuditObserver] = WeakSet()


class AuditObserver:
    """每个订阅独立的有界 FIFO，排队失败不反向阻塞对话。"""

    def __init__(self, name: str, callback: Callable[..., Any], *, queue_size: int = 128) -> None:
        if type(queue_size) is not int or not 1 <= queue_size <= 4096:
            raise ValueError("审计队列长度必须为 1 到 4096 的整数")
        self.name = name
        self.callback = callback
        self.queue: asyncio.Queue[tuple[str, ChatHookContext, Mapping[str, Any]]] = asyncio.Queue(queue_size)
        self.worker: asyncio.Task[None] | None = None
        self.closed = False
        self.dropped = 0
        _observers.add(self)

    def __call__(self, event: str, context: ChatHookContext, details: Mapping[str, Any]) -> None:
        """仅入队，队满丢弃新通知并限频报告累计丢弃量。"""
        if self.closed:
            return
        try:
            self.queue.put_nowait((event, context, details))
        except asyncio.QueueFull:
            self.dropped += 1
            if self.dropped & (self.dropped - 1) == 0:
                logger.warning("[ChatAudit] 审计队列已满: {} dropped={}", self.name, self.dropped)
            return
        if self.worker is None:
            # 审计不能继承第一条消息的 Bot、权限或轮次追踪上下文。
            self.worker = Context().run(asyncio.create_task, self._run(), name=f"chat-audit:{self.name}")

    async def _run(self) -> None:
        """按入队顺序处理，空闲时退出，回调异常与超时只影响当前通知。"""
        from .hooks import _call

        try:
            while not self.closed and not self.queue.empty():
                event, context, details = self.queue.get_nowait()
                try:
                    await _call(self.callback, event, context, details)
                except Exception:
                    logger.opt(exception=True).warning("[ChatAudit] 审计回调失败: {}/{}", self.name, event)
                finally:
                    self.queue.task_done()
        finally:
            if self.worker is asyncio.current_task():
                self.worker = None
            if self.closed:
                _observers.discard(self)

    def close(self) -> None:
        """卸载时立即撤销排队通知和工作任务，重复调用安全。"""
        if self.closed:
            return
        self.closed = True
        while not self.queue.empty():
            self.queue.get_nowait()
            self.queue.task_done()
        if self.worker is not None:
            self.worker.cancel()
            self.worker.add_done_callback(lambda done: _observers.discard(self))
        else:
            _observers.discard(self)

    async def stop(self) -> None:
        """关闭框架时等待工作任务取消完成，不排空可能很慢的审计回调。"""
        worker = self.worker
        self.close()
        if worker is not None and worker is not asyncio.current_task():
            await asyncio.gather(worker, return_exceptions=True)


async def stop_audit_observers() -> None:
    """在共享资源释放前统一取消全部后台审计。"""
    await asyncio.gather(*(observer.stop() for observer in tuple(_observers)))
