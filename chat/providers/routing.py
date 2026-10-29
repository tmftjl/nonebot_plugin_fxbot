"""任务级 Provider 路由与调用边界回退。"""

from __future__ import annotations

import asyncio
from typing import Any
from contextlib import AsyncExitStack

from nonebot import logger

from .usage import track_model_task_usage
from .manager import provider_manager
from ...config import get_manager as get_config_manager
from ..bindings import PROVIDER, get_binding

_MAX_CANDIDATES = 2
_RETRYABLE_NAMES = frozenset(
    {
        "APIConnectionError",
        "APITimeoutError",
        "ConnectError",
        "ConnectTimeout",
        "NetworkError",
        "ReadError",
        "ReadTimeout",
        "RemoteProtocolError",
        "ServiceUnavailableError",
    }
)


def is_retryable(exc: BaseException) -> bool:
    """只把超时、网络错误与 5xx 视为可换 Provider 的错误。"""
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError, ConnectionError)):
        return True
    status = getattr(exc, "status_code", None)
    if not isinstance(status, int):
        status = getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(status, int):
        return status >= 500
    return type(exc).__name__ in _RETRYABLE_NAMES


def candidate_providers(task: str, session_id: str = "") -> list[str]:
    """任务路由 → 会话绑定 → 默认 Provider，再接配置的 fallback，最多两个。"""
    try:
        chat = get_config_manager().get_system()["chat"]
    except Exception:
        return [""]
    routing = chat.get("model_routing") or {}
    primary = str(routing.get(task) or "") or get_binding(PROVIDER, session_id)
    fallback = chat.get("provider_fallback") or []
    available = set(provider_manager.list_providers_by_type("chat"))
    candidates: list[str] = []
    for name in [primary, *[str(item) for item in fallback]]:
        if name and name not in available:
            continue
        if name not in candidates:
            candidates.append(name)
    return (candidates or [""])[:_MAX_CANDIDATES]


def routed_task(task: str, fallback: str = "") -> str:
    """任务未单独配置时沿用 fallback 任务，避免新增任务键改变旧配置。"""
    if not fallback:
        return task
    try:
        routing = get_config_manager().get_system()["chat"].get("model_routing") or {}
        configured = str(routing.get(task) or "").strip() if isinstance(routing, dict) else ""
    except Exception:
        return fallback
    return task if configured else fallback


class FallbackProvider:
    """在单次模型调用边界按候选顺序重试；已出现工具结果时不再切换。"""

    def __init__(self, candidates: list[str], *, task: str = "") -> None:
        self._candidates = candidates or [""]
        self._index = 0
        self._stack = AsyncExitStack()
        self._provider: Any = None
        self.task = task

    async def __aenter__(self) -> FallbackProvider:
        self._provider = await self._stack.enter_async_context(provider_manager.lease(self._candidates[0]))
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._stack.aclose()

    @property
    def name(self) -> str:
        """当前实际 Provider 名称；空字符串表示配置的默认 Provider。"""
        return str(getattr(self._provider, "provider_id", "") or self._candidates[self._index])

    def __getattr__(self, item: str) -> Any:
        provider = self.__dict__.get("_provider")
        if provider is None:
            raise AttributeError(item)
        return getattr(provider, item)

    async def _switch(self) -> None:
        previous = self._candidates[self._index]
        self._index += 1
        name = self._candidates[self._index]
        logger.warning(f"[Provider] {self.task or 'chat'} 调用失败，回退 {previous or '<默认>'} → {name or '<默认>'}")
        self._provider = await self._stack.enter_async_context(provider_manager.lease(name))

    def _can_fallback(self, messages: Any, exc: BaseException) -> bool:
        if not is_retryable(exc) or self._index + 1 >= len(self._candidates):
            return False
        return not any(str(message.get("role")) == "tool" for message in messages or [] if isinstance(message, dict))

    async def chat(self, *args: Any, **kwargs: Any) -> Any:
        """普通对话；仅对可重试错误换到下一个候选。"""
        messages = kwargs.get("messages") or (args[0] if args else [])
        while True:
            try:
                async with track_model_task_usage(self.task, self._provider):
                    return await self._provider.chat(*args, **kwargs)
            except Exception as exc:
                if not self._can_fallback(messages, exc):
                    raise
                await self._switch()

    async def chat_json(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """结构化输出；错误类型判断与普通对话一致。"""
        messages = kwargs.get("messages") or (args[0] if args else [])
        while True:
            try:
                async with track_model_task_usage(self.task, self._provider):
                    return await self._provider.chat_json(*args, **kwargs)
            except Exception as exc:
                if not self._can_fallback(messages, exc):
                    raise
                await self._switch()


def fallback_provider(task: str, session_id: str = "") -> FallbackProvider:
    """按任务与租户构造带回退的 Provider 上下文。"""
    return FallbackProvider(candidate_providers(task, session_id), task=task)
