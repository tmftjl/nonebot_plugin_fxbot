"""会话历史存储。"""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any


class ChatSessionStore:
    """内存会话历史。"""

    def __init__(self, max_messages: int = 20) -> None:
        self.max_messages = max_messages
        self._store: dict[str, deque[dict[str, Any]]] = defaultdict(
            lambda: deque(maxlen=self.max_messages)
        )

    def get(self, session_id: str) -> list[dict[str, Any]]:
        """获取会话历史。"""
        return list(self._store[session_id])

    def append(self, session_id: str, message: dict[str, Any]) -> None:
        """追加一条消息。"""
        self._store[session_id].append(message)

    def replace(self, session_id: str, messages: list[dict[str, Any]]) -> None:
        """替换会话历史。"""
        self._store[session_id] = deque(messages[-self.max_messages :], maxlen=self.max_messages)

    def clear(self, session_id: str) -> None:
        """清空会话历史。"""
        self._store.pop(session_id, None)


default_session_store = ChatSessionStore()
