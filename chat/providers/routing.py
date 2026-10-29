"""按任务和会话选择单一 Provider，并记录任务用量。"""

from __future__ import annotations

from typing import Any
from contextlib import AsyncExitStack

from .usage import track_model_task_usage
from .manager import provider_manager
from ...config import get_manager as get_config_manager
from ..bindings import PROVIDER, get_binding


def candidate_providers(task: str, session_id: str = "") -> list[str]:
    """任务分配优先，其次会话绑定，最后使用默认 Provider。"""
    chat = get_config_manager().get_system()["chat"]
    routing = chat.get("model_routing") or {}
    primary = str(routing.get(task) or "") or get_binding(PROVIDER, session_id)
    available = set(provider_manager.list_providers_by_type("chat"))
    return [primary if not primary or primary in available else ""]


def routed_task(task: str, fallback: str = "") -> str:
    """任务未单独配置时沿用调用方指定的任务。"""
    if not fallback:
        return task
    try:
        routing = get_config_manager().get_system()["chat"].get("model_routing") or {}
        configured = str(routing.get(task) or "").strip() if isinstance(routing, dict) else ""
    except Exception:
        return fallback
    return task if configured else fallback


class TaskProvider:
    """维护单一任务模型的租约与用量上下文，不执行故障转移。"""

    def __init__(self, candidates: list[str], *, task: str = "") -> None:
        self._name = (candidates or [""])[0]
        self._stack = AsyncExitStack()
        self._provider: Any = None
        self.task = task

    async def __aenter__(self) -> TaskProvider:
        self._provider = await self._stack.enter_async_context(provider_manager.lease(self._name))
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._stack.aclose()

    @property
    def name(self) -> str:
        """返回实际 Provider 名称。"""
        return str(getattr(self._provider, "provider_id", "") or self._name)

    def __getattr__(self, item: str) -> Any:
        provider = self.__dict__.get("_provider")
        if provider is None:
            raise AttributeError(item)
        return getattr(provider, item)

    async def chat(self, *args: Any, **kwargs: Any) -> Any:
        """使用选定 Provider 生成回复。"""
        async with track_model_task_usage(self.task, self._provider):
            return await self._provider.chat(*args, **kwargs)

    async def chat_json(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """使用选定 Provider 生成结构化内容。"""
        async with track_model_task_usage(self.task, self._provider):
            return await self._provider.chat_json(*args, **kwargs)

    async def chat_stream(self, *args: Any, **kwargs: Any):
        """保留流式用量上下文，结束或取消时关闭流。"""
        async with track_model_task_usage(self.task, self._provider):
            stream = self._provider.chat_stream(*args, **kwargs)
            try:
                async for response in stream:
                    yield response
            finally:
                await stream.aclose()


def fallback_provider(task: str, session_id: str = "") -> TaskProvider:
    """按任务分配及会话绑定构造单一模型上下文。"""
    return TaskProvider(candidate_providers(task, session_id), task=task)
