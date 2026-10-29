"""会话历史存储。"""

from __future__ import annotations

import time
import asyncio
from typing import Any
from collections import OrderedDict, deque, defaultdict

from ..config import SYSTEM_DEFAULTS, get_manager as get_config_manager

_IDLE_TTL = 24 * 60 * 60
_MAX_SESSIONS = 2000
_CLEANUP_INTERVAL = 32


def history_limit(chat_cfg: dict[str, Any], is_group: bool) -> int:
    """群聊与私聊各自保留给模型的消息条数。"""
    key = "max_context_size" if is_group else "max_private_context_size"
    value = chat_cfg.get(key, SYSTEM_DEFAULTS["chat"][key])
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def history_enabled(is_group: bool) -> bool:
    return history_limit(get_config_manager().get_system()["chat"], is_group) > 0


class ChatSessionStore:
    """内存会话历史。"""

    def __init__(self, max_messages: int = 20) -> None:
        self.max_messages = max_messages
        self._store: dict[str, deque[dict[str, Any]]] = {}
        self._limits: dict[str, int] = {}
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._turn_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._send_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._epochs: dict[str, int] = {}
        self._seen: dict[str, OrderedDict[str, None]] = {}
        self._seq_counters: dict[str, int] = {}
        self._user_seqs: dict[tuple[str, str], int] = {}
        self._activities: dict[str, int] = {}
        self._dropped: dict[str, list[dict[str, Any]]] = {}
        self._hydrated: set[str] = set()
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
            locks = (
                self._locks.get(candidate),
                self._turn_locks.get(candidate),
                self._send_locks.get(candidate),
            )
            if any(
                lock is not None and (lock.locked() or any(not waiter.cancelled() for waiter in (lock._waiters or ())))
                for lock in locks
            ):
                continue
            for mapping in (
                self._store,
                self._limits,
                self._locks,
                self._turn_locks,
                self._send_locks,
                self._epochs,
                self._seen,
                self._dropped,
                self._accessed,
            ):
                mapping.pop(candidate, None)
            self._seq_counters.pop(candidate, None)
            self._activities.pop(candidate, None)
            for key in [key for key in self._user_seqs if key[0] == candidate]:
                self._user_seqs.pop(key, None)
            self._hydrated.discard(candidate)

    def mark_seen(self, session_id: str, dedupe_key: str) -> bool:
        """首次记录消息时返回 True，每会话按先进先出保留最多 256 个键。"""
        self._touch(session_id)
        seen = self._seen.setdefault(session_id, OrderedDict())
        if dedupe_key in seen:
            return False
        seen[dedupe_key] = None
        self._activities[session_id] = self._activities.get(session_id, 0) + 1
        if len(seen) > 256:
            seen.popitem(last=False)
        return True

    def lock(self, session_id: str) -> asyncio.Lock:
        """返回会话级互斥锁，避免并发消息互相覆盖历史。"""
        self._touch(session_id)
        return self._locks[session_id]

    def turn_lock(self, session_id: str) -> asyncio.Lock:
        """返回独立轮次锁，按到达顺序串行处理同一会话的触发。"""
        self._touch(session_id)
        return self._turn_locks[session_id]

    def send_lock(self, session_id: str) -> asyncio.Lock:
        """返回发送锁；同一会话的多段回复不允许与其他轮次交错。"""
        self._touch(session_id)
        return self._send_locks[session_id]

    def activity(self, session_id: str) -> int:
        """返回会话内已接收的唯一消息计数，用于判断发送时是否有更新消息。"""
        self._touch(session_id)
        return self._activities.get(session_id, 0)

    def epoch(self, session_id: str) -> int:
        """返回会话代次；清空后递增，用于丢弃清空前发出的回复。"""
        self._touch(session_id)
        return self._epochs.get(session_id, 0)

    def set_epoch(self, session_id: str, epoch: int) -> None:
        """与持久化代次对齐，避免重启后旧代次被当成当前轮次。"""
        self._touch(session_id)
        self._epochs[session_id] = int(epoch)

    def next_seq(self, session_id: str, user_id: str) -> int:
        """为一次触发分配单调递增序号，并记录该用户的最新序号。"""
        self._touch(session_id)
        seq = self._seq_counters.get(session_id, 0) + 1
        self._seq_counters[session_id] = seq
        self._user_seqs[(session_id, user_id)] = seq
        return seq

    def is_latest_seq(self, session_id: str, user_id: str, seq: int) -> bool:
        """该触发之后没有同一用户的新触发。"""
        self._touch(session_id)
        return bool(seq) and self._user_seqs.get((session_id, user_id)) == seq

    def is_hydrated(self, session_id: str) -> bool:
        """是否已从数据库恢复过该会话。"""
        self._touch(session_id)
        return session_id in self._hydrated

    def mark_hydrated(self, session_id: str) -> None:
        """标记该会话已尝试从数据库恢复。"""
        self._touch(session_id)
        self._hydrated.add(session_id)

    def _deque(self, session_id: str, limit: int | None) -> deque[dict[str, Any]]:
        """按当前上限获取队列，上限变化时重建以立即生效。"""
        size = max(int(self._limits.get(session_id, self.max_messages) if limit is None else limit), 0)
        self._touch(session_id)
        if limit is not None:
            self._limits[session_id] = size
        if size == 0:
            self._dropped.pop(session_id, None)
            self._store[session_id] = deque(maxlen=0)
            return self._store[session_id]
        current = self._store.get(session_id)
        if current is None or current.maxlen != size:
            previous = list(current or ())
            overflow = max(len(previous) - size, 0)
            self._dropped.setdefault(session_id, []).extend(previous[:overflow])
            current = deque(previous[overflow:], maxlen=size)
            self._store[session_id] = current
        self._trim_head(session_id, current)
        return current

    def _trim_head(self, session_id: str, history: deque[dict[str, Any]]) -> None:
        while history and history[0].get("role") != "user":
            self._dropped.setdefault(session_id, []).append(history.popleft())

    def get(self, session_id: str) -> list[dict[str, Any]]:
        """获取会话历史。"""
        self._touch(session_id)
        return list(self._store.get(session_id, ()))

    def trim(self, session_id: str, limit: int) -> None:
        """按当前上限裁剪会话历史。"""
        self._deque(session_id, limit)

    def take_dropped(self, session_id: str) -> list[dict[str, Any]]:
        """取走因窗口或孤立工具结果被挤掉的消息，供上下文压缩使用。"""
        self._touch(session_id)
        return self._dropped.pop(session_id, [])

    def append(self, session_id: str, message: dict[str, Any], *, limit: int | None = None) -> None:
        """追加一条消息。"""
        self.append_many(session_id, [message], limit=limit)

    def append_many(self, session_id: str, messages: list[dict[str, Any]], *, limit: int | None = None) -> None:
        """在调用方的状态锁内成批追加，裁剪后不保留孤立工具结果。"""
        history = self._deque(session_id, limit)
        if history.maxlen == 0:
            return
        if history.maxlen is not None:
            combined = [*history, *messages]
            overflow = max(len(combined) - history.maxlen, 0)
            self._dropped.setdefault(session_id, []).extend(combined[:overflow])
        history.extend(messages)
        self._trim_head(session_id, history)

    def discard_message(self, session_id: str, message_id: str) -> bool:
        """按平台消息 ID 丢弃内存里已经带入的历史，包括等待压缩的旧消息。"""
        key = str(message_id)
        dropped = False
        history = self._store.get(session_id)
        if history:
            kept = [item for item in history if key not in (item.get("_source_ids") or ())]
            if len(kept) != len(history):
                self._store[session_id] = deque(kept, maxlen=history.maxlen)
                dropped = True
        waiting = self._dropped.get(session_id)
        if waiting:
            kept = [item for item in waiting if key not in (item.get("_source_ids") or ())]
            if len(kept) != len(waiting):
                self._dropped[session_id] = kept
                dropped = True
        return dropped

    def invalidate(self, session_id: str) -> int:
        """让在途轮次作废但保留历史；返回新的代次。"""
        self._touch(session_id)
        epoch = self._epochs.get(session_id, 0) + 1
        self._epochs[session_id] = epoch
        return epoch

    def clear(self, session_id: str) -> None:
        """清空会话历史与触发序号。"""
        self._store.pop(session_id, None)
        self._limits.pop(session_id, None)
        self._seen.pop(session_id, None)
        self._dropped.pop(session_id, None)
        self._seq_counters.pop(session_id, None)
        self._activities.pop(session_id, None)
        for key in [key for key in self._user_seqs if key[0] == session_id]:
            self._user_seqs.pop(key, None)
        self._epochs[session_id] = self.epoch(session_id) + 1


default_session_store = ChatSessionStore()
