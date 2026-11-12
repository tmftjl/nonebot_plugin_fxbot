"""按任务和会话选择单一 Provider，并记录任务用量。"""

from __future__ import annotations

import asyncio
from time import perf_counter
from typing import Any
from contextlib import AsyncExitStack
from collections.abc import AsyncGenerator

from .usage import track_model_task_usage
from .manager import provider_manager
from ...config import get_manager as get_config_manager
from .entities import TokenUsage, LLMResponse, JSONResponse, LLMResponseError
from ..bindings import PROVIDER, get_binding
from ..model_trace import record_model_call


def _call_argument(args: tuple[Any, ...], kwargs: dict[str, Any], name: str, index: int) -> Any:
    """优先取关键字参数，其次按 Provider 方法签名位置回退。"""
    if name in kwargs:
        return kwargs[name]
    return args[index] if len(args) > index else None


def candidate_providers(task: str, session_id: str = "") -> list[str]:
    """会话绑定优先，其次任务分配，最后使用默认 Provider。"""
    chat = get_config_manager().get_system()["chat"]
    routing = chat.get("model_routing") or {}
    available = set(provider_manager.list_providers_by_type("chat"))
    for primary in (get_binding(PROVIDER, session_id), str(routing.get(task) or "")):
        if primary in available:
            return [primary]
    return [""]


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
    """维护单一任务模型的租约与显式用量汇总，不执行故障转移。"""

    def __init__(self, candidates: list[str], *, task: str = "") -> None:
        self._name = (candidates or [""])[0]
        self._stack = AsyncExitStack()
        self._provider: Any = None
        self.task = task
        self.usage = TokenUsage()

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

    async def chat(self, *args: Any, **kwargs: Any) -> LLMResponse:
        """使用选定 Provider 生成回复，并落一份请求快照。"""
        merge_into = str(kwargs.pop("_trace_merge", "") or "")
        workflow_purpose = str(kwargs.pop("_trace_purpose", "") or "")
        workflow_attempt = int(kwargs.pop("_trace_attempt", 1) or 1)
        started = perf_counter()
        response: LLMResponse | None = None
        error = ""
        try:
            async with track_model_task_usage(self.task, self._provider):
                response = await self._provider.chat(*args, **kwargs)
            return response
        except (Exception, asyncio.CancelledError) as exc:
            if isinstance(exc, LLMResponseError):
                response = exc.response
            error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            if response is not None:
                self.usage.merge(response.usage)
            key = self._record(
                messages=_call_argument(args, kwargs, "messages", 0),
                tools=_call_argument(args, kwargs, "tools", 3),
                response=response,
                duration_ms=(perf_counter() - started) * 1000,
                error=error,
                workflow_purpose=workflow_purpose,
                workflow_attempt=workflow_attempt,
                merge_into=merge_into,
                previous_purpose="replyer.reasoning_initial" if merge_into else "",
            )
            if key and response is not None:
                response._trace_key = key

    async def chat_continuation(self, *args: Any, **kwargs: Any) -> LLMResponse | None:
        """续写一次「只有思考没有正文」的响应；不支持续写的 Provider 返回 None。"""
        method = getattr(self._provider, "chat_continuation", None)
        if method is None:
            return None
        started = perf_counter()
        response: LLMResponse | None = None
        error = ""
        try:
            async with track_model_task_usage(self.task, self._provider):
                response = await method(*args, **kwargs)
            return response
        except (Exception, asyncio.CancelledError) as exc:
            if isinstance(exc, LLMResponseError):
                response = exc.response
            error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            if response is not None:
                self.usage.merge(response.usage)
            if response is not None or error:
                previous = _call_argument(args, kwargs, "previous", 1)
                replay = (getattr(previous, "provider_data", None) or {}).get("output_items") or []
                merge_into = str(getattr(previous, "_trace_key", "") or "")
                key = self._record(
                    messages=[*(_call_argument(args, kwargs, "messages", 0) or []), *replay],
                    tools=None,
                    response=response,
                    duration_ms=(perf_counter() - started) * 1000,
                    error=error,
                    workflow_purpose="replyer.reasoning_continuation" if merge_into else "",
                    workflow_attempt=0,
                    merge_into=merge_into,
                    previous_purpose="replyer.reasoning_initial" if merge_into else "",
                )
                if key and response is not None:
                    response._trace_key = key

    async def chat_json(self, *args: Any, **kwargs: Any) -> JSONResponse:
        """使用选定 Provider 生成结构化内容。"""
        started = perf_counter()
        response: LLMResponse | None = None
        error = ""
        try:
            async with track_model_task_usage(self.task, self._provider):
                result = await self._provider.chat_json(*args, **kwargs)
                response = result.response
            return result
        except (Exception, asyncio.CancelledError) as exc:
            if isinstance(exc, LLMResponseError):
                response = exc.response
            error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            if response is not None:
                self.usage.merge(response.usage)
            self._record(
                messages=_call_argument(args, kwargs, "messages", 0),
                tools=None,
                response=response,
                duration_ms=(perf_counter() - started) * 1000,
                error=error,
            )

    async def chat_stream(self, *args: Any, **kwargs: Any) -> AsyncGenerator[LLMResponse, None]:
        """结束或取消时关闭流，并汇总最终响应的用量和快照。"""
        started = perf_counter()
        chunks: list[str] = []
        final: LLMResponse | None = None
        error = ""
        try:
            async with track_model_task_usage(self.task, self._provider):
                stream = self._provider.chat_stream(*args, **kwargs)
                try:
                    async for response in stream:
                        if response.is_stream_chunk:
                            chunks.append(str(response.content or ""))
                        else:
                            final = response
                        yield response
                finally:
                    await stream.aclose()
        except (Exception, asyncio.CancelledError, GeneratorExit) as exc:
            if isinstance(exc, LLMResponseError):
                final = exc.response
            error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            response = final or LLMResponse(content="".join(chunks))
            self.usage.merge(response.usage)
            key = self._record(
                messages=_call_argument(args, kwargs, "messages", 0),
                tools=_call_argument(args, kwargs, "tools", 3),
                response=response,
                duration_ms=(perf_counter() - started) * 1000,
                error=error,
            )
            if key:
                response._trace_key = key

    def _record(
        self,
        *,
        messages: Any,
        tools: Any,
        response: LLMResponse | None,
        duration_ms: float,
        error: str,
        workflow_purpose: str = "",
        workflow_attempt: int = 1,
        merge_into: str = "",
        previous_purpose: str = "",
    ) -> str:
        """写入请求快照；失败不影响模型调用。"""
        provider_name = str(getattr(self._provider, "provider_id", "") or self._name)
        try:
            model = str(self._provider.get_model() or "")
        except Exception:
            model = ""
        return record_model_call(
            task=self.task,
            provider=provider_name,
            model=model,
            messages=messages,
            tools=tools,
            response=response,
            duration_ms=int(duration_ms),
            error=error,
            workflow_purpose=workflow_purpose,
            workflow_attempt=workflow_attempt,
            merge_into=merge_into,
            previous_purpose=previous_purpose,
        )


def fallback_provider(task: str, session_id: str = "") -> TaskProvider:
    """按会话绑定、任务分配构造单一模型上下文。"""
    return TaskProvider(candidate_providers(task, session_id), task=task)
