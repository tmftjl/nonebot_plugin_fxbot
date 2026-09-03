"""平台适配器 SPI 注册与工厂。"""

from __future__ import annotations

from typing import Any

from .interfaces import PlatformAdapter, PlatformError, UnsupportedCapability

_adapters: list[PlatformAdapter] = []


def adapter_name(bot: Any) -> str:
    adapter = getattr(bot, "adapter", None)
    return (
        str(adapter.get_name())
        if adapter and hasattr(adapter, "get_name")
        else str(getattr(bot, "type", "Unknown"))
    )


def register_adapter(adapter: PlatformAdapter | type[PlatformAdapter]):
    instance = adapter() if isinstance(adapter, type) else adapter
    _adapters.append(instance)
    return adapter


def get_platform_adapter(bot: Any) -> PlatformAdapter:
    for adapter in _adapters:
        if adapter.match(bot):
            return adapter
    raise PlatformError(f"未注册的平台适配器: {getattr(bot, 'type', type(bot).__name__)}")
