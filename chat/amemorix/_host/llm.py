"""A_Memorix LLM 桥接。

A_Memorix 只认识 MaiBot 的任务模型抽象（``TaskConfig`` / ``LLMServiceClient``）。
这里把它映射到 FxBot 的 Provider 层：任务名按 FxBot ``chat.model_routing`` 的
任务键选择 Provider，调用统一走 ``fallback_provider``。
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional
from dataclasses import field, dataclass

from ....config import get_manager
from .person_db import current_scope
from .host_config import TaskConfig
from ...providers.usage import track_model_task_usage
from ...providers.manager import provider_manager
from ...providers.routing import routed_task, fallback_provider

TASK_ROUTES: Dict[str, str] = {
    "memory": "memory",
    "utils": "utils",
    "learner": "learner",
    "lpmm_entity_extract": "utils",
    "lpmm_rdf_build": "utils",
    "replyer": "reply",
    "tool_use": "reply",
    "planner": "planner",
    "emoji": "emoji",
}
ROUTE_FALLBACKS: Dict[str, str] = {
    "memory": "utils",
    "learner": "utils",
    "emoji": "vision",
}
DEFAULT_ROUTE = "utils"


def _task_defaults(task_name: str, model_list: List[str] | None = None) -> TaskConfig:
    """沿用 MaiBot 默认任务参数，模型选择由 FxBot 路由提供。"""
    temperature = {"utils": 0.5, "memory": 0.3, "replyer": 1.0, "fast_model": 0.3}.get(task_name, 0.7)
    return TaskConfig(
        model_list=model_list or [],
        temperature=temperature,
        max_tokens=16384 if task_name == "planner" else 8192,
        hard_timeout={"memory": 240.0, "replyer": 240.0, "planner": 180.0}.get(task_name, 120.0),
        selection_strategy="random" if task_name in {"utils", "memory", "replyer", "planner"} else "balance",
    )


@dataclass
class LLMGenerationOptions:
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None


@dataclass
class LLMServiceRequest:
    task_name: str = ""
    request_type: str = ""
    prompt: str = ""
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    session_id: str = ""


@dataclass
class LLMCompletion:
    """保留 MaiBot 结果对象上的 ``response`` 字段。"""

    response: str = ""
    model: str = ""


@dataclass
class LLMServiceResult:
    success: bool
    completion: Optional[LLMCompletion] = None
    error: str = ""

    @classmethod
    def from_response_result(cls, completion: Any) -> "LLMServiceResult":
        if isinstance(completion, LLMCompletion):
            return cls(True, completion)
        return cls(True, LLMCompletion(response=str(getattr(completion, "response", "") or "")))

    @classmethod
    def from_error(cls, error: str, detail: str = "") -> "LLMServiceResult":
        message = " ".join(part for part in (str(error or ""), str(detail or "")) if part).strip()
        return cls(False, None, message)


def _chat_config() -> Dict[str, Any]:
    try:
        return get_manager().get_system().get("chat", {}) or {}
    except Exception:
        return {}


def _chat_providers() -> Dict[str, Dict[str, Any]]:
    raw = _chat_config().get("providers")
    if not isinstance(raw, dict):
        return {}
    return {
        str(name): value
        for name, value in raw.items()
        if isinstance(value, dict) and str(value.get("provider_type") or "chat") == "chat"
    }


def _provider_model(provider_name: str) -> str:
    provider = _chat_providers().get(provider_name) or {}
    return str(provider.get("model") or "").strip()


def _route_task(route: str) -> str:
    routing = _chat_config().get("model_routing")
    if not isinstance(routing, dict):
        return ""
    return str(routing.get(route) or "").strip()


def _route_provider(route: str) -> str:
    providers = _chat_providers()
    configured = _route_task(route)
    if configured and configured in providers:
        return configured
    default = str(_chat_config().get("provider") or "").strip()
    if default and default in providers:
        return default
    return next(iter(providers), "")


def _route_model(route: str) -> str:
    provider_name = _route_provider(route)
    return _provider_model(provider_name) if provider_name else ""


def resolve_route(task_name: str) -> str:
    route = TASK_ROUTES.get(str(task_name or "").strip(), DEFAULT_ROUTE)
    return routed_task(route, ROUTE_FALLBACKS.get(route, ""))


def get_available_models() -> Dict[str, Any]:
    """返回 A_Memorix 可用的文本生成任务；没有任何对话 Provider 时为空。"""
    providers = _chat_providers()
    if not providers:
        return {}
    models: Dict[str, Any] = {}
    for task_name in ("memory", "utils", "learner", "lpmm_entity_extract", "lpmm_rdf_build"):
        model = _route_model(resolve_route(task_name))
        models[task_name] = _task_defaults(task_name, [model or "auto"])
    for task_name in ("planner", "replyer", "tool_use", "emoji"):
        model = _route_model(resolve_route(task_name))
        if model:
            models[task_name] = _task_defaults(task_name, [model])
    return models


class _ClientOrchestrator:
    """兼容 ``model_routing`` 直接改写模型选择的内部接口。"""

    def __init__(self) -> None:
        self.model_for_task: Optional[TaskConfig] = None
        self.model_usage: Dict[str, Any] = {}

    def _refresh_task_config(self) -> Optional[TaskConfig]:
        return self.model_for_task


class LLMServiceClient:
    def __init__(
        self,
        task_name: str = "",
        request_type: str = "",
        session_id: str = "",
        **_kwargs: Any,
    ) -> None:
        self.task_name = task_name
        self.request_type = request_type
        self.session_id = session_id
        self._orchestrator = _ClientOrchestrator()

    async def generate_response(
        self,
        prompt: str | None = None,
        options: Optional[LLMGenerationOptions] = None,
        *,
        messages: Optional[List[Dict[str, Any]]] = None,
        **_kwargs: Any,
    ) -> LLMCompletion:
        options = options or LLMGenerationOptions()
        task_config = self._orchestrator.model_for_task or _task_defaults(self.task_name)
        model = ""
        if task_config is not None:
            candidates = [str(item).strip() for item in getattr(task_config, "model_list", []) or []]
            model = next((item for item in candidates if item and item != "auto"), "")

        temperature = options.temperature
        if temperature is None:
            temperature = float(task_config.temperature)
        max_tokens = options.max_tokens
        if max_tokens is None and task_config is not None:
            max_tokens = int(getattr(task_config, "max_tokens", 0) or 0) or None

        chat_messages = messages or [{"role": "user", "content": prompt or ""}]
        session_id = self.session_id or current_scope()
        async with (
            asyncio.timeout(task_config.hard_timeout),
            fallback_provider(resolve_route(self.task_name), session_id) as provider,
        ):
            response = await provider.chat(
                chat_messages,
                model=model or None,
                temperature=float(temperature),
                max_tokens=max_tokens,
            )
        return LLMCompletion(
            response=str(getattr(response, "content", "") or ""),
            model=str(getattr(provider, "name", "") or ""),
        )


async def generate(request: LLMServiceRequest) -> LLMServiceResult:
    client = LLMServiceClient(
        task_name=request.task_name, request_type=request.request_type, session_id=request.session_id
    )
    try:
        completion = await client.generate_response(
            request.prompt,
            LLMGenerationOptions(temperature=request.temperature, max_tokens=request.max_tokens),
        )
        return LLMServiceResult.from_response_result(completion)
    except Exception as exc:
        return LLMServiceResult.from_error(f"生成内容时出错: {exc}", str(exc))


@dataclass
class EmbeddingResult:
    embedding: List[float] = field(default_factory=list)
    model_name: str = ""
    model_identifier: str = ""
    api_provider: str = ""
    request_protocol_hash: str = ""


def embedding_provider_name(task: str = "embedding") -> str:
    """返回任务指定的嵌入 Provider，留空或无效时取第一个；未配置时抛错让上层降级。"""
    raw = _chat_config().get("providers")
    providers = raw if isinstance(raw, dict) else {}
    configured = _route_task(task)
    if configured and str((providers.get(configured) or {}).get("provider_type") or "") == "embedding":
        return configured
    for name, value in providers.items():
        if isinstance(value, dict) and str(value.get("provider_type") or "") == "embedding":
            return str(name)
    raise RuntimeError("FxBot 未配置嵌入 Provider")


async def embed_texts(texts: List[str]) -> List[EmbeddingResult]:
    provider_name = embedding_provider_name()
    provider = await provider_manager.get_embedding_provider(provider_name)
    async with track_model_task_usage("embedding", provider):
        vectors = await provider.embed(texts)
    model_name = str(getattr(provider, "model_name", "") or "")
    return [
        EmbeddingResult(
            embedding=list(vector),
            model_name=model_name,
            model_identifier=model_name,
            api_provider=provider_name,
        )
        for vector in vectors
    ]


class EmbeddingServiceClient:
    """图片嵌入入口；Provider 未实现 ``embed_image`` 时明确失败，由调用方降级。"""

    def __init__(self, task_name: str = "", request_type: str = "", session_id: str = "") -> None:
        self.task_name = task_name
        self.request_type = request_type
        self.session_id = session_id

    async def embed_texts(self, texts: List[str]) -> List[EmbeddingResult]:
        return await embed_texts(texts)

    async def embed_image(self, image_bytes: bytes, **_kwargs: Any) -> EmbeddingResult:
        name = embedding_provider_name("image_embedding")
        provider = await provider_manager.get_embedding_provider(name)
        embed_image = getattr(provider, "embed_image", None)
        if embed_image is None:
            raise RuntimeError(f"嵌入 Provider {name} 不支持图片嵌入")
        model_name = str(getattr(provider, "model_name", "") or "")
        async with track_model_task_usage("embedding", provider):
            return EmbeddingResult(
                embedding=list(await embed_image(image_bytes)),
                model_name=model_name,
                model_identifier=model_name,
                api_provider=name,
            )
