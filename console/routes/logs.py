"""运行日志实时推送路由。"""

from __future__ import annotations

import json
import asyncio
from typing import TYPE_CHECKING, Any

from fastapi import Depends, Request, APIRouter
from fastapi.responses import StreamingResponse

from ..auth import sse_auth
from ..log_stream import boot_id, snapshot, subscribe, unsubscribe

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_HEARTBEAT_SECONDS = 15.0

router = APIRouter(prefix="/logs", tags=["fxbot-logs"], dependencies=[Depends(sse_auth)])


def _frame(item: dict[str, Any]) -> str:
    """把一条日志记录序列化成 SSE 帧。

    id 里带上进程标识：浏览器重连会把 id 原样作为 Last-Event-ID 回传，服务端据此判断
    游标是否属于本进程。否则进程重启后浏览器带着上个进程的 seq 回来，会把新进程 seq
    更小的记录（也就是刚重启那批启动日志）整体过滤掉。
    """
    return f"id: {item['boot']}:{item['seq']}\ndata: {json.dumps(item, ensure_ascii=False)}\n\n"


def _resume_seq(last_event_id: str) -> int | None:
    """解析 Last-Event-ID 游标；非本进程或格式非法时返回 None，表示整体重放。"""
    boot, sep, seq_text = last_event_id.partition(":")
    if not sep or boot != boot_id() or not seq_text.isdigit():
        return None
    return int(seq_text)


@router.get("/stream")
async def stream_logs(request: Request) -> StreamingResponse:
    """以 SSE 持续推送运行日志。"""
    after_seq = _resume_seq(request.headers.get("last-event-id", ""))

    async def event_stream() -> AsyncIterator[str]:
        # 先注册订阅再取快照，否则两步之间产生的日志会永久丢失
        queue = subscribe()
        try:
            for item in snapshot(after_seq):
                yield _frame(item)
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=_HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    # 空闲期发注释帧保活，EventSource 会忽略以冒号开头的行
                    yield ": ping\n\n"
                    continue
                yield _frame(item)
        finally:
            # 客户端断开时生成器被关闭，不摘队列会让订阅者集合持续增长
            unsubscribe(queue)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
