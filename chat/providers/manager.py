"""Provider 管理器，Provider source 导入失败不得影响主插件启动。"""

from __future__ import annotations

from typing import Any

from nonebot import logger

from ...config import get_manager as get_config_manager

from .base import BaseProvider, ChatProvider, EmbeddingProvider
from .register import get_provider_class, provider_registry


def _provider_configs() -> dict[str, dict[str, Any]]:
    """读取系统配置中的 Provider 配置。"""
    cfg = get_config_manager().get_system()
    chat_cfg = cfg.get("chat") if isinstance(cfg.get("chat"), dict) else {}
    providers = chat_cfg.get("providers") if isinstance(chat_cfg.get("providers"), dict) else {}
    return {str(k): v for k, v in providers.items() if isinstance(v, dict)}


def _default_provider_name() -> str:
    """读取默认对话 Provider 名称。"""
    cfg = get_config_manager().get_system()
    chat_cfg = cfg.get("chat") if isinstance(cfg.get("chat"), dict) else {}
    return str(chat_cfg.get("provider") or "")


class ProviderManager:
    """Provider 管理器。"""

    _instance: "ProviderManager | None" = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._providers: dict[str, BaseProvider] = {}
        self._initialized = True
        self.import_sources()

    def import_sources(self) -> None:
        """导入所有 Provider source 以触发注册装饰器。"""
        from . import sources  # noqa: F401

    def get_provider(self, name: str = "") -> ChatProvider:
        """获取对话 Provider。"""
        provider_name, config = self._resolve_config(name, capability="chat")
        if provider_name in self._providers:
            provider = self._providers[provider_name]
            if isinstance(provider, ChatProvider):
                return provider

        provider = self._create_provider(provider_name, config)
        if not isinstance(provider, ChatProvider):
            raise TypeError(f"Provider {provider_name} 不是 ChatProvider")
        self._providers[provider_name] = provider
        return provider

    def get_embedding_provider(self, name: str = "") -> EmbeddingProvider:
        """获取嵌入 Provider。"""
        provider_name, config = self._resolve_config(name, capability="embedding")
        cache_key = f"embedding:{provider_name}"
        if cache_key in self._providers:
            provider = self._providers[cache_key]
            if isinstance(provider, EmbeddingProvider):
                return provider

        provider = self._create_provider(provider_name, config)
        if not isinstance(provider, EmbeddingProvider):
            raise TypeError(f"Provider {provider_name} 不是 EmbeddingProvider")
        self._providers[cache_key] = provider
        return provider

    def _resolve_config(self, name: str, *, capability: str) -> tuple[str, dict[str, Any]]:
        configs = _provider_configs()
        provider_name = name or _default_provider_name()

        if provider_name and provider_name in configs:
            return provider_name, configs[provider_name]

        for candidate, config in configs.items():
            if str(config.get("provider_type", "chat")) == capability:
                return candidate, config

        raise ValueError(f"未找到 {capability} Provider 配置: {name or '<default>'}")

    def _create_provider(self, name: str, config: dict[str, Any]) -> BaseProvider:
        """创建 Provider 实例。"""
        api_type = str(config.get("type") or "openai")
        capability_type = str(config.get("provider_type") or "chat")
        provider_type = f"{api_type}_{capability_type}"

        try:
            cls = get_provider_class(provider_type)
        except ValueError as exc:
            raise ValueError(
                f"Provider 类型 '{provider_type}' 未注册，请检查配置或可选 SDK 是否已安装"
            ) from exc

        instance = cls(provider_id=name, config=config)
        logger.debug(f"[ProviderManager] 已加载: {name} (type={provider_type})")
        return instance

    def reset(self) -> None:
        """清空已缓存的 Provider。"""
        count = len(self._providers)
        self._providers.clear()
        logger.info(f"[ProviderManager] 已重置 {count} 个 Provider")

    def list_cached(self) -> dict[str, str]:
        """列出已缓存的 Provider。"""
        return {k: type(v).__name__ for k, v in self._providers.items()}

    def list_registered(self) -> dict[str, str]:
        """列出已注册的 Provider 类型。"""
        return {name: meta.display_name or name for name, meta in provider_registry.items()}

    def list_providers_by_type(self, provider_type: str = "chat") -> list[str]:
        """按能力类型列出配置中的 Provider 名称。"""
        return [
            name
            for name, config in _provider_configs().items()
            if str(config.get("provider_type", "chat")) == provider_type
        ]

    def get_api_config(self, name: str) -> dict[str, Any] | None:
        """获取指定 Provider 的配置。"""
        return _provider_configs().get(name)


provider_manager = ProviderManager()
