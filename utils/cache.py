"""进程内缓存，提供惰性过期、LRU 淘汰及按域统计。"""

from __future__ import annotations

import sys
import time
from typing import Any, TypedDict
from collections import OrderedDict
from dataclasses import fields, dataclass, is_dataclass
from collections.abc import Callable, Hashable

_MISSING = object()


class _Stats(TypedDict):
    """单个域的当前条目数和累计操作计数。"""

    entries: int
    hits: int
    misses: int
    evictions: int
    expired: int
    sets: int


class _Snapshot(_Stats):
    """控制台展示的域统计及估算内存。"""

    domain: str
    bytes: int


@dataclass(slots=True)
class _Entry:
    """缓存值及过期、创建和访问时间。"""

    value: Any
    expires_at: float | None
    created_at: float
    accessed_at: float


def estimate_size(value: Any, depth: int = 4) -> int:
    """按深度估算对象占用字节数，同一次遍历中按对象身份去重。"""
    seen: set[int] = set()

    def measure(current: Any, remaining: int) -> int:
        """递归累加浅层大小及受深度限制的子对象大小。"""
        identity = id(current)
        if identity in seen:
            return 0
        seen.add(identity)
        size = sys.getsizeof(current)
        if remaining <= 0:
            return size
        if isinstance(current, dict):
            size += sum(measure(key, remaining - 1) + measure(item, remaining - 1) for key, item in current.items())
        elif isinstance(current, (list, tuple, set, frozenset)):
            size += sum(measure(item, remaining - 1) for item in current)
        else:
            attributes = getattr(current, "__dict__", None)
            if isinstance(attributes, dict):
                size += measure(attributes, remaining - 1)
            if is_dataclass(current) and not isinstance(current, type):
                size += sum(measure(getattr(current, field.name), remaining - 1) for field in fields(current))
            for base in type(current).__mro__:
                slots = base.__dict__.get("__slots__", ())
                if isinstance(slots, str):
                    slots = (slots,)
                for name in slots:
                    if name in {"__dict__", "__weakref__"}:
                        continue
                    if name.startswith("__") and not name.endswith("__"):
                        name = "_" + base.__name__.lstrip("_") + name
                    item = getattr(current, name, _MISSING)
                    if item is not _MISSING:
                        size += measure(item, remaining - 1)
        return size

    return measure(value, depth)


def key_label(key: Hashable) -> str:
    """将元组键按斜杠连接，其余键直接转换为字符串。"""
    return "/".join(map(str, key)) if isinstance(key, tuple) else str(key)


class Cache:
    """单进程同步缓存，统计累计保留且不提供并发加载合并。"""

    def __init__(self, maxsize: int = 4096) -> None:
        """设置正整数容量上限并初始化缓存及统计。"""
        if not isinstance(maxsize, int) or isinstance(maxsize, bool) or maxsize <= 0:
            raise ValueError("maxsize 必须是正整数")
        self._maxsize = maxsize
        self._entries: OrderedDict[Hashable, _Entry] = OrderedDict()
        self._counters: dict[str, _Stats] = {}

    @staticmethod
    def _domain(key: Hashable) -> str:
        """使用非空元组键的首项字符串作为域，其余归入 misc。"""
        return str(key[0]) if isinstance(key, tuple) and key else "misc"

    def _counts(self, key: Hashable) -> _Stats:
        """获取键所属域的累计计数，不存在时初始化。"""
        domain = self._domain(key)
        if domain not in self._counters:
            self._counters[domain] = _Stats(entries=0, hits=0, misses=0, evictions=0, expired=0, sets=0)
        return self._counters[domain]

    def _purge_expired(self) -> None:
        """惰性删除所有到期条目并累计其所属域的过期数。"""
        now = time.monotonic()
        expired = [
            key for key, entry in self._entries.items() if entry.expires_at is not None and entry.expires_at <= now
        ]
        for key in expired:
            del self._entries[key]
            self._counts(key)["expired"] += 1

    def get(self, key: Hashable, default: Any = None) -> Any:
        """读取条目，命中时刷新访问时间和 LRU，过期视为未命中。"""
        entry = self._entries.get(key)
        counts = self._counts(key)
        if entry is not None and entry.expires_at is not None and entry.expires_at <= time.monotonic():
            del self._entries[key]
            counts["expired"] += 1
            entry = None
        if entry is None:
            counts["misses"] += 1
            return default
        counts["hits"] += 1
        entry.accessed_at = time.time()
        self._entries.move_to_end(key)
        return entry.value

    def set(self, key: Hashable, value: Any, ttl: float | None = None) -> None:
        """写入并刷新 LRU，TTL 单位为秒，非正值立即过期。"""
        now = time.time()
        expires_at = None if ttl is None else time.monotonic() + ttl
        self._entries[key] = _Entry(value, expires_at, now, now)
        self._entries.move_to_end(key)
        self._counts(key)["sets"] += 1
        if ttl is not None and ttl <= 0:
            del self._entries[key]
            self._counts(key)["expired"] += 1
            return
        if len(self._entries) > self._maxsize:
            self._purge_expired()
        while len(self._entries) > self._maxsize:
            evicted_key, _ = self._entries.popitem(last=False)
            self._counts(evicted_key)["evictions"] += 1

    def pop(self, key: Hashable, default: Any = None) -> Any:
        """移除并返回未过期值，不计读取命中或未命中。"""
        entry = self._entries.pop(key, None)
        if entry is None:
            return default
        if entry.expires_at is not None and entry.expires_at <= time.monotonic():
            self._counts(key)["expired"] += 1
            return default
        return entry.value

    def clear(self) -> None:
        """删除所有条目，保留累计操作统计。"""
        self._entries.clear()

    def __len__(self) -> int:
        """清理过期条目后返回当前有效条目数。"""
        self._purge_expired()
        return len(self._entries)

    def remember(self, key: Hashable, loader: Callable[[], Any], ttl: float | None = None) -> Any:
        """仅未命中时同步加载并缓存，允许空值且原样传播加载异常。"""
        value = self.get(key, _MISSING)
        if value is _MISSING:
            value = loader()
            self.set(key, value, ttl)
        return value

    def clear_prefix(self, prefix: tuple[Any, ...]) -> int:
        """删除以指定元组前缀开头的元组键，返回实际删除数量。"""
        keys = [key for key in self._entries if isinstance(key, tuple) and key[: len(prefix)] == prefix]
        for key in keys:
            del self._entries[key]
        return len(keys)

    def keys(self, domain: str | None = None) -> list[tuple[Hashable, float | None, float, float]]:
        """返回有效键及时间信息快照，可按域过滤且不刷新访问时间。"""
        self._purge_expired()
        return [
            (key, entry.expires_at, entry.created_at, entry.accessed_at)
            for key, entry in self._entries.items()
            if domain is None or self._domain(key) == domain
        ]

    def items(self, domain: str | None = None) -> list[tuple[Hashable, Any, float | None, float, float]]:
        """返回有效条目快照，不刷新访问时间也不改变统计。"""
        self._purge_expired()
        return [
            (key, entry.value, entry.expires_at, entry.created_at, entry.accessed_at)
            for key, entry in self._entries.items()
            if domain is None or self._domain(key) == domain
        ]

    def stats(self) -> dict[str, _Stats]:
        """返回按域聚合的有效条目数及累计统计副本。"""
        self._purge_expired()
        result = {domain: counts.copy() for domain, counts in self._counters.items()}
        for key in self._entries:
            result[self._domain(key)]["entries"] += 1
        return result

    def snapshot(self) -> list[_Snapshot]:
        """返回控制台统计列表，字节数按各条目的键和值分别估算后累加。"""
        stats = self.stats()
        sizes = dict.fromkeys(stats, 0)
        for key, entry in self._entries.items():
            sizes[self._domain(key)] += estimate_size(key) + estimate_size(entry.value)
        return [_Snapshot(domain=domain, bytes=sizes[domain], **counts) for domain, counts in stats.items()]


cache = Cache()
