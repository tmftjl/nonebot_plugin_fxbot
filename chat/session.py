"""会话历史存储。"""

from __future__ import annotations

import time
import asyncio
from typing import Any
from collections import OrderedDict, deque, defaultdict

_IDLE_TTL = 24 * 60 * 60
_MAX_SESSIONS = 2000
_CLEANUP_INTERVAL = 32


class ChatSessionStore:
    """内存会话历史。"""

    def __init__(self, max_messages: int = 20) -> None:
        self.max_messages = max_messages
        self._store: dict[str, deque[dict[str, Any]]] = {}
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._turn_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._epochs: dict[str, int] = {}
        self._accessed: OrderedDict[str, float] = OrderedDict()
        self._access_count = 0

    def _touch(self, session_id: str) -> None:
        now = time.monotonic()
        self._accessed[session_id] = now
        self._accessed.move_to_end(session_id)
        self._access_count += 1
        if self._access_count % _CLEANUP_INTERVAL and len(self._accessed) <= _MAX_SESSIONS:
            return
        for candidate, accessed in list(self._accessed.items()):
            if now - accessed < _IDLE_TTL and len(self._accessed) <= _MAX_SESSIONS:
                break
            if candidate == session_id:
                continue
            locks = (self._locks.get(candidate), self._turn_locks.get(candidate))
            if any(
                lock is not None and (lock.locked() or any(not waiter.cancelled() for waiter in (lock._waiters or ())))
                for lock in locks
            ):
                continue
            for mapping in (self._store, self._locks, self._turn_locks, self._epochs, self._accessed):
                mapping.pop(candidate, None)

    def lock(self, session_id: str) -> asyncio.Lock:
        """返回会话级互斥锁，避免并发消息互相覆盖历史。"""
        self._touch(session_id)
        return self._locks[session_id]

    def turn_lock(self, session_id: str) -> asyncio.Lock:
        """返回独立轮次锁，按到达顺序串行处理同一会话的触发。"""
        self._touch(session_id)
        return self._turn_locks[session_id]

    def epoch(self, session_id: str) -> int:
        """返回会话代次；清空后递增，用于丢弃清空前发出的回复。"""
        self._touch(session_id)
        return self._epochs.get(session_id, 0)

    def _deque(self, session_id: str, limit: int | None) -> deque[dict[str, Any]]:
        """按当前上限获取队列，上限变化时重建以立即生效。"""
        size = max(int(self.max_messages if limit is None else limit), 0)
        self._touch(session_id)
        current = self._store.get(session_id)
        if current is None or current.maxlen != size:
            current = deque(current or (), maxlen=size)
            self._store[session_id] = current
        self._trim_head(current)
        return current

    @staticmethod
    def _trim_head(history: deque[dict[str, Any]]) -> None:
        while history and history[0].get("role") != "user":
            history.popleft()

    def get(self, session_id: str) -> list[dict[str, Any]]:
        """获取会话历史。"""
        self._touch(session_id)
        return list(self._store.get(session_id, ()))

    def trim(self, session_id: str, limit: int) -> None:
        """按当前上限裁剪会话历史。"""
        self._deque(session_id, limit)

    def append(self, session_id: str, message: dict[str, Any], *, limit: int | None = None) -> None:
        """追加一条消息。"""
        self.append_many(session_id, [message], limit=limit)

    def append_many(self, session_id: str, messages: list[dict[str, Any]], *, limit: int | None = None) -> None:
        """在调用方的状态锁内成批追加，裁剪后不保留孤立工具结果。"""
        history = self._deque(session_id, limit)
        history.extend(messages)
        self._trim_head(history)

    def clear(self, session_id: str) -> None:
        """清空会话历史。"""
        self._store.pop(session_id, None)
        self._epochs[session_id] = self.epoch(session_id) + 1


default_session_store = ChatSessionStore()
