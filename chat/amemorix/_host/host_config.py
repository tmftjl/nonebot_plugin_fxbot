"""A_Memorix 宿主配置桥接。

配置保存在 FxBot 的 ``config/amemorix/config.json``，字段与默认值沿用 MaiBot
``AMemorixConfig``。读取时按文件 mtime 热更新，调用方每次取到的都是最新配置。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from nonebot import logger

from ....config import get_manager
from ....utils.paths import config_dir
from .amemorix_config import AMemorixConfig

NAMESPACE = "amemorix"
FILENAME = "config.json"
BOT_CONFIG_PATH = config_dir("system") / "config.json"

_cache: tuple[float, AMemorixConfig] | None = None
_reload_callbacks: list[Callable[..., Any]] = []


@dataclass
class TaskConfig:
    """与 MaiBot ``TaskConfig`` 字段和默认值一致的任务模型配置。"""

    model_list: list[str] = field(default_factory=list)
    max_tokens: int = 4096
    temperature: float = 0.7
    selection_strategy: str = "balance"
    hard_timeout: float = 240.0


@dataclass(frozen=True)
class ModelInfo:
    """宿主模型条目，供嵌入适配层读取。"""

    name: str
    model_identifier: str = ""
    api_provider: str = ""
    extra_params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class APIProvider:
    """宿主 Provider 条目，供嵌入适配层读取。"""

    name: str
    base_url: str = ""


class _EmbeddingTaskView:
    """仅暴露嵌入任务候选模型。"""

    def __init__(self, model_list: list[str]) -> None:
        self.model_list = model_list


class _ModelTaskConfigView:
    def __init__(self, embedding_models: list[str]) -> None:
        self.embedding = _EmbeddingTaskView(embedding_models)


class ModelConfigView:
    """把 FxBot 的 Provider 配置映射成 MaiBot 的模型配置视图。"""

    def __init__(self) -> None:
        chat = _chat_config()
        providers = chat.get("providers") if isinstance(chat.get("providers"), dict) else {}
        models: list[ModelInfo] = []
        api_providers: list[APIProvider] = []
        embedding_models: list[str] = []
        for name, raw in providers.items():
            if not isinstance(raw, dict):
                continue
            model_name = str(raw.get("model") or "").strip()
            provider_type = str(raw.get("provider_type") or "chat")
            models.append(
                ModelInfo(
                    name=model_name,
                    model_identifier=model_name,
                    api_provider=str(name),
                )
            )
            api_providers.append(
                APIProvider(name=str(name), base_url=str(raw.get("base_url") or ""))
            )
            if provider_type == "embedding" and model_name:
                embedding_models.append(model_name)
        self.models = models
        self.api_providers = api_providers
        self.model_task_config = _ModelTaskConfigView(embedding_models)
        self.models_dict = {item.name: item for item in models if item.name}


class _ConfigManagerBridge:
    """A_Memorix 用到的宿主配置管理器接口。"""

    @staticmethod
    def get_model_config() -> ModelConfigView:
        return ModelConfigView()

    @staticmethod
    def get_global_config() -> Any:
        return global_config

    @staticmethod
    def register_reload_callback(callback: Callable[..., Any], sections: Sequence[str] | None = None) -> None:
        if callback not in _reload_callbacks:
            _reload_callbacks.append(callback)

    @staticmethod
    def unregister_reload_callback(callback: Callable[..., Any]) -> None:
        if callback in _reload_callbacks:
            _reload_callbacks.remove(callback)

    @staticmethod
    def reload_config(changed_scopes: Iterable[str] = (), **_kwargs: Any) -> bool:
        notify_reload_callbacks(tuple(changed_scopes))
        return True


config_manager = _ConfigManagerBridge()


def notify_reload_callbacks(changed_scopes: Sequence[str] = ("a_memorix",)) -> None:
    """配置热更新后通知已注册回调；回调异常不影响配置读取。"""
    global _cache
    _cache = None
    for callback in list(_reload_callbacks):
        try:
            callback(changed_scopes)
        except Exception as exc:
            logger.warning(f"[A_Memorix] 配置重载回调失败: {exc}")


def _chat_config() -> dict[str, Any]:
    try:
        return get_manager().get_system().get("chat", {}) or {}
    except Exception as exc:
        logger.warning(f"[A_Memorix] 读取 FxBot 聊天配置失败: {exc}")
        return {}


def _default_config() -> dict[str, Any]:
    data = AMemorixConfig().model_dump()
    data.setdefault("storage", {})["data_dir"] = "a-memorix"
    return data


def _validate(raw: dict[str, Any]) -> None:
    AMemorixConfig(**raw)


def _proxy():
    return get_manager().register(
        NAMESPACE,
        _default_config(),
        filename=FILENAME,
        clean_extra=True,
        validator=_validate,
    )


def load_amemorix_config() -> AMemorixConfig:
    """返回当前生效的 A_Memorix 配置，文件变化后自动重建。"""
    global _cache
    proxy = _proxy()
    mtime = proxy.storage.mtime()
    if _cache is None or _cache[0] != mtime:
        _cache = (mtime, AMemorixConfig(**proxy.load()))
    return _cache[1]


def save_amemorix_config(raw: dict[str, Any]) -> AMemorixConfig:
    """校验并保存配置；保存后通知内核重建配置视图。"""
    config = AMemorixConfig(**raw)
    _proxy().save(config.model_dump())
    notify_reload_callbacks()
    return load_amemorix_config()


def get_config_path() -> Path:
    return _proxy().path


class _BotView:
    @property
    def nickname(self) -> str:
        from ....chat.personas import DEFAULT_PERSONA

        return str(_chat_config().get("persona") or "").strip() or DEFAULT_PERSONA


class _PersonalityView:
    @property
    def personality(self) -> str:
        from ....chat.personas import DEFAULT_PERSONA, _split_sections, load_personas

        name = str(_chat_config().get("persona") or "").strip() or DEFAULT_PERSONA
        personas = load_personas()
        text = str(personas.get(name) or personas.get(DEFAULT_PERSONA) or "").strip()
        if not text:
            return ""
        parsed = _split_sections(text)
        if parsed is None:
            return text
        intro, sections = parsed
        return sections.get("personality") or intro or text


class _AMemorixView:
    @property
    def integration(self):
        return load_amemorix_config().integration

    def __getattr__(self, item: str) -> Any:
        return getattr(load_amemorix_config(), item)


class _GlobalConfigView:
    def __init__(self) -> None:
        self.a_memorix = _AMemorixView()
        self.bot = _BotView()
        self.personality = _PersonalityView()


global_config = _GlobalConfigView()
model_config = ModelConfigView()
