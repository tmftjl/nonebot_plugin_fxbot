"""会话历史存储。"""

from __future__ import annotations

import asyncio
from typing import Any
from collections import deque, defaultdict


def _trim(messages: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """截取历史窗口，从 user 消息开始并丢弃尾部未完成的工具调用。"""
    if limit <= 0:
        return []
    window = messages[-limit:]
    for index, message in enumerate(window):
        if message.get("role") == "user":
            window = window[index:]
            break
    else:
        window = [message for message in window if message.get("role") == "system"]
    while window and window[-1].get("role") == "assistant" and window[-1].get("tool_calls"):
        window.pop()
    return window


class ChatSessionStore:
    """内存会话历史。"""

    def __init__(self, max_messages: int = 20) -> None:
        self.max_messages = max_messages
        self._store: dict[str, deque[dict[str, Any]]] = {}
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    def lock(self, session_id: str) -> asyncio.Lock:
        """返回会话级互斥锁，避免并发消息互相覆盖历史。"""
        return self._locks[session_id]

    def _deque(self, session_id: str, limit: int | None) -> deque[dict[str, Any]]:
        """按当前上限获取队列，上限变化时重建以立即生效。"""
        size = max(int(self.max_messages if limit is None else limit), 0)
        current = self._store.get(session_id)
        if current is None or current.maxlen != size:
            current = deque(current or (), maxlen=size)
            self._store[session_id] = current
        return current

    def get(self, session_id: str) -> list[dict[str, Any]]:
        """获取会话历史。"""
        return list(self._store.get(session_id, ()))

    def append(self, session_id: str, message: dict[str, Any], *, limit: int | None = None) -> None:
        """追加一条消息。"""
        self._deque(session_id, limit).append(message)

    def replace(self, session_id: str, messages: list[dict[str, Any]], *, limit: int | None = None) -> None:
        """替换会话历史。"""
        size = max(int(self.max_messages if limit is None else limit), 0)
        self._store[session_id] = deque(_trim(messages, size), maxlen=size)

    def clear(self, session_id: str) -> None:
        """清空会话历史。"""
        self._store.pop(session_id, None)


default_session_store = ChatSessionStore()
