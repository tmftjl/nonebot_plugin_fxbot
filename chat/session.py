"""会话历史存储。"""

from __future__ import annotations

import asyncio
from typing import Any
from collections import deque, defaultdict


class ChatSessionStore:
    """内存会话历史。"""

    def __init__(self, max_messages: int = 20) -> None:
        self.max_messages = max_messages
        self._store: dict[str, deque[dict[str, Any]]] = {}
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._epochs: dict[str, int] = {}

    def lock(self, session_id: str) -> asyncio.Lock:
        """返回会话级互斥锁，避免并发消息互相覆盖历史。"""
        return self._locks[session_id]

    def epoch(self, session_id: str) -> int:
        """返回会话代次；清空后递增，用于丢弃清空前发出的回复。"""
        return self._epochs.get(session_id, 0)

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

    def clear(self, session_id: str) -> None:
        """清空会话历史。"""
        self._store.pop(session_id, None)
        self._epochs[session_id] = self.epoch(session_id) + 1


default_session_store = ChatSessionStore()
