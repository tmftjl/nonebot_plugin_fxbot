"""聊天 Hook 的可重复微基准，只比较本地派发开销，不代表模型请求端到端延迟。"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from types import MappingProxyType
from timeit import repeat
from statistics import median

from .chat import support as support


def legacy_snapshot(value):
    """保留优化前的快照算法，供同一进程内对照。"""
    if isinstance(value, dict):
        return MappingProxyType({key: legacy_snapshot(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(legacy_snapshot(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(legacy_snapshot(item) for item in value)
    return deepcopy(value)


def compare(label, before, after, *, number):
    """打印多次测量的中位数，避免单次计时噪声主导结果。"""
    old = median(repeat(before, number=number, repeat=5)) / number * 1_000_000
    new = median(repeat(after, number=number, repeat=5)) / number * 1_000_000
    print(f"{label}: {old:.2f} -> {new:.2f} us/op ({old / new:.2f}x)")


async def dispatch_comparison():
    """对比同样十个空通知订阅者下的完整派发。"""
    from src.plugins.nonebot_plugin_fxbot.chat import audit, hooks, extensions

    context = hooks.ChatHookContext("bench:bot:private:user")
    details = {"items": [{"id": index, "text": "sample", "tags": ["a", "b"]} for index in range(100)]}
    extension = extensions.ChatExtension("benchmark")

    def callback(*args):
        return None

    for index in range(10):
        extension.on("observer", callback, key=str(index))
    entries = dict(hooks._hooks["observer"])

    async def old_dispatch():
        for _, entry in sorted(entries.items(), key=lambda item: item[1].priority):
            await hooks._call(entry, "event", context, legacy_snapshot(details))

    async def measure(operation):
        loop = asyncio.get_running_loop()
        samples = []
        for _ in range(5):
            started = loop.time()
            for _ in range(300):
                await operation()
            samples.append((loop.time() - started) / 300 * 1_000_000)
        return median(samples)

    try:
        old = await measure(old_dispatch)
        new = await measure(lambda: hooks.notify("event", context, **details))
        print(f"notify/10 observers/100 items: {old:.2f} -> {new:.2f} us/op ({old / new:.2f}x)")
    finally:
        extension.close()
        await audit.stop_audit_observers()


def main():
    """运行注册读取和通知派发基准，不修改持久配置或发起模型请求。"""
    from src.plugins.nonebot_plugin_fxbot.chat.hook_registry import (
        CallbackRegistry,
        ordered_callbacks,
        register_callback,
    )

    registry = CallbackRegistry()
    for index in range(20):
        register_callback(registry, str(index), lambda: None, priority=index % 3)
    entries = dict(registry)
    compare(
        "ordered registry/20 callbacks",
        lambda: tuple(sorted(entries.items(), key=lambda item: getattr(item[1], "priority", 100))),
        lambda: ordered_callbacks(registry),
        number=100_000,
    )
    asyncio.run(dispatch_comparison())


if __name__ == "__main__":
    main()
