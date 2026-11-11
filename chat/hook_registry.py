"""聊天各类扩展共用的有序注册与精确注销机制。"""

from __future__ import annotations

import inspect
from typing import Any
from dataclasses import dataclass
from collections.abc import Callable, Iterator, MutableMapping

Callback = Callable[..., Any]
CallbackSnapshot = tuple[tuple[str, Callback], ...]


class CallbackRegistry(MutableMapping[str, Callback]):
    """注册变化时重建有序快照，派发只读取现成元组。"""

    def __init__(self) -> None:
        self._entries: dict[str, Callback] = {}
        self.snapshot: CallbackSnapshot = ()

    def __getitem__(self, key: str) -> Callback:
        return self._entries[key]

    def __setitem__(self, key: str, value: Callback) -> None:
        self._entries[key] = value
        self._rebuild()

    def __delitem__(self, key: str) -> None:
        del self._entries[key]
        self._rebuild()

    def __iter__(self) -> Iterator[str]:
        return iter(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def _rebuild(self) -> None:
        """同优先级稳定排序，旧快照在本次派发结束前继续有效。"""
        self.snapshot = tuple(sorted(self._entries.items(), key=lambda item: getattr(item[1], "priority", 100)))


@dataclass(frozen=True)
class Registration:
    """每次注册拥有独立身份，重复注册同一个函数也不会被旧句柄移除。"""

    callback: Callable[..., Any]
    priority: int = 100
    events: frozenset[str] | None = None

    def __call__(self, *args: Any) -> Any:
        """保留原回调的参数和返回值语义。"""
        return self.callback(*args)


def register_callback(
    entries: MutableMapping[str, Callback],
    name: str,
    callback: Callable[..., Any],
    *,
    priority: int = 100,
    synchronous: bool = False,
    events: frozenset[str] | None = None,
) -> Callable[[], None]:
    """校验并注册回调，同优先级按注册顺序运行，同名替换保留位置。"""
    if not isinstance(name, str) or not name.strip() or not callable(callback) or type(priority) is not int:
        raise ValueError("扩展需要非空名称、可调用对象和整数优先级")
    if synchronous and (
        inspect.iscoroutinefunction(callback) or inspect.iscoroutinefunction(getattr(callback, "__call__", None))
    ):
        raise ValueError("该扩展必须同步")
    registration = Registration(callback, priority, events)
    entries[name] = registration

    def unregister() -> None:
        """仅移除本次注册，重复注销及热重载均安全。"""
        if entries.get(name) is registration:
            entries.pop(name)

    return unregister


def ordered_callbacks(entries: MutableMapping[str, Callback]) -> CallbackSnapshot:
    """固定本次派发快照，期间注册或注销只影响下一次派发。"""
    if isinstance(entries, CallbackRegistry):
        return entries.snapshot
    if not entries:
        return ()
    return tuple(sorted(entries.items(), key=lambda item: getattr(item[1], "priority", 100)))
