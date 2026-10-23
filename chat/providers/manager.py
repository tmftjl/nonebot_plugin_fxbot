"""Provider 管理器，Provider source 导入失败不得影响主插件启动。"""

from __future__ import annotations

import asyncio
from typing import Any
from contextlib import asynccontextmanager
from dataclasses import dataclass
from collections.abc import AsyncIterator

from nonebot import logger

from .base import BaseProvider, ChatProvider, EmbeddingProvider
from ...config import get_manager as get_config_manager
from .register import provider_registry, get_provider_class


@dataclass
class _ProviderLease:
    provider: BaseProvider
    references: int = 0
    retired: bool = False
    closing: bool = False


def _provider_configs() -> dict[str, dict[str, Any]]:
    """读取系统配置中的 Provider 配置。"""
    cfg = get_config_manager().get_system()
    chat_cfg = cfg["chat"]
    providers = chat_cfg["providers"]
    return {str(k): v for k, v in providers.items() if isinstance(v, dict)}


def _default_provider_name() -> str:
    """读取默认对话 Provider 名称。"""
    cfg = get_config_manager().get_system()
    chat_cfg = cfg["chat"]
    return str(chat_cfg["provider"] or "")


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
        self._leases: dict[int, _ProviderLease] = {}
        self._closing: set[asyncio.Task[None]] = set()
        self._lock = asyncio.Lock()
        self._initialized = True
        self.import_sources()

    def import_sources(self) -> None:
        """导入所有 Provider source 以触发注册装饰器。"""
        from . import sources  # noqa: F401

    async def get_provider(self, name: str = "") -> ChatProvider:
        """获取对话 Provider，配置变化时自动重建。"""
        state = await self._get(name, capability="chat")
        return state.provider

    @asynccontextmanager
    async def lease(self, name: str = "") -> AsyncIterator[ChatProvider]:
        """租用对话实例，退役实例在最后一个在途请求结束后释放。"""
        state = await self._get(name, capability="chat", acquire=True)
        try:
            yield state.provider
        finally:
            async with self._lock:
                state.references -= 1
                self._close_retired(state)

    def _close_retired(self, state: _ProviderLease) -> None:
        if not state.retired or state.references or state.closing:
            return
        state.closing = True
        self._leases.pop(id(state.provider), None)
        task = asyncio.create_task(self._release(state.provider))
        self._closing.add(task)
        task.add_done_callback(self._closing.discard)

    async def _get(self, name: str, *, capability: str, acquire: bool = False) -> _ProviderLease:
        async with self._lock:
            provider_name, config = self._resolve_config(name, capability=capability)
            cache_key = provider_name if capability == "chat" else f"embedding:{provider_name}"
            expected = ChatProvider if capability == "chat" else EmbeddingProvider
            cached = self._providers.get(cache_key)
            if isinstance(cached, expected) and cached.config == config:
                state = self._leases[id(cached)]
            else:
                provider = self._create_provider(provider_name, config)
                state = _ProviderLease(provider)
                if not isinstance(provider, expected):
                    state.retired = True
                    self._close_retired(state)
                    raise TypeError(f"Provider {provider_name} 不是 {expected.__name__}")
                self._providers[cache_key] = provider
                self._leases[id(provider)] = state
                if cached is not None:
                    retired = self._leases[id(cached)]
                    retired.retired = True
                    self._close_retired(retired)
            if acquire:
                state.references += 1
            return state

    async def get_embedding_provider(self, name: str = "") -> EmbeddingProvider:
        """获取嵌入 Provider，配置变化时自动重建。"""
        state = await self._get(name, capability="embedding")
        return state.provider

    def _resolve_config(self, name: str, *, capability: str) -> tuple[str, dict[str, Any]]:
        configs = _provider_configs()
        provider_name = name or _default_provider_name()

        if provider_name and provider_name in configs:
            return provider_name, configs[provider_name]

        for candidate, config in configs.items():
            if str(config["provider_type"]) == capability:
                return candidate, config

        raise ValueError(f"未找到 {capability} Provider 配置: {name or '<default>'}")

    def _create_provider(self, name: str, config: dict[str, Any]) -> BaseProvider:
        """创建 Provider 实例。"""
        api_type = str(config["type"])
        capability_type = str(config["provider_type"])
        provider_type = f"{api_type}_{capability_type}"

        try:
            cls = get_provider_class(provider_type)
        except ValueError as exc:
            raise ValueError(f"Provider 类型 '{provider_type}' 未注册，请检查配置或可选 SDK 是否已安装") from exc

        instance = cls(provider_id=name, config=config)
        logger.debug(f"[ProviderManager] 已加载: {name} (type={provider_type})")
        return instance

    async def _release(self, provider: BaseProvider) -> None:
        """释放 Provider 持有的资源。"""
        try:
            await provider.close()
        except Exception:
            logger.opt(exception=True).warning(f"[ProviderManager] 关闭 {provider.provider_id} 失败")

    async def reset(self) -> None:
        """退役并清空缓存；在途租约释放后再关闭对应实例。"""
        async with self._lock:
            providers = list(self._providers.values())
            self._providers.clear()
            for provider in providers:
                state = self._leases[id(provider)]
                state.retired = True
                self._close_retired(state)
            closing = list(self._closing)
        if closing:
            await asyncio.gather(*(asyncio.shield(task) for task in closing))
        logger.info(f"[ProviderManager] 已重置 {len(providers)} 个 Provider")

    def list_cached(self) -> dict[str, str]:
        """列出已缓存的 Provider。"""
        return {k: type(v).__name__ for k, v in self._providers.items()}

    def list_registered(self) -> dict[str, str]:
        """列出已注册的 Provider 类型。"""
        return {name: meta.display_name or name for name, meta in provider_registry.items()}

    def list_providers_by_type(self, provider_type: str = "chat") -> list[str]:
        """按能力类型列出配置中的 Provider 名称。"""
        return [name for name, config in _provider_configs().items() if str(config["provider_type"]) == provider_type]

    def get_api_config(self, name: str) -> dict[str, Any] | None:
        """获取指定 Provider 的配置。"""
        return _provider_configs().get(name)


provider_manager = ProviderManager()
