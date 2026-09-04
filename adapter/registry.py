"""平台适配器 SPI 注册与工厂。"""

from __future__ import annotations

from typing import Any

from nonebot.log import logger

from .interfaces import PlatformAdapter, PlatformError

_adapters: list[PlatformAdapter] = []


def get_registered_adapters() -> tuple[PlatformAdapter, ...]:
    return tuple(_adapters)


def adapter_name(bot: Any) -> str:
    adapter = getattr(bot, "adapter", None)
    return (
        str(adapter.get_name())
        if adapter and hasattr(adapter, "get_name")
        else str(getattr(bot, "type", "Unknown"))
    )


def register_adapter(adapter: PlatformAdapter | type[PlatformAdapter]):
    instance = adapter() if isinstance(adapter, type) else adapter
    if not any(type(item) is type(instance) for item in _adapters):
        _adapters.append(instance)
    return adapter


def get_platform_adapter(bot: Any) -> PlatformAdapter:
    for adapter in _adapters:
        if adapter.match(bot):
            return adapter
    name = getattr(bot, "type", type(bot).__name__)
    logger.info(f"[adapter] 未注册的平台适配器，忽略事件: {name}")
    raise PlatformError(f"未注册的平台适配器: {name}")
