"""Provider 注册系统，按 temp/core/agent/providers/register.py 适配。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .entities import ProviderMetadata, ProviderType

if TYPE_CHECKING:
    from .base import BaseProvider

provider_registry: dict[str, ProviderMetadata] = {}


def register_provider(
    type_name: str,
    desc: str = "",
    provider_type: ProviderType = ProviderType.CHAT,
    default_config: dict[str, Any] | None = None,
    display_name: str | None = None,
):
    """Provider 注册装饰器。"""

    def decorator(cls: type["BaseProvider"]):
        if type_name in provider_registry:
            return cls

        provider_registry[type_name] = ProviderMetadata(
            id="default",
            model=None,
            type=type_name,
            desc=desc,
            provider_type=provider_type,
            cls_type=cls,
            default_config=default_config,
            display_name=display_name or type_name,
        )
        return cls

    return decorator


def get_provider_class(type_name: str) -> type["BaseProvider"]:
    """获取已注册的 Provider 类。"""
    meta = provider_registry.get(type_name)
    if not meta:
        raise ValueError(f"未注册的 Provider 类型: {type_name}")
    return meta.cls_type


def list_providers() -> dict[str, ProviderMetadata]:
    """列出所有已注册的 Provider。"""
    return provider_registry.copy()
