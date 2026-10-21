"""AI Provider 配置路由。"""

from __future__ import annotations

import re
from typing import Any

from fastapi import Depends, APIRouter
from nonebot import logger

from ..auth import bearer_auth
from ...config import SYSTEM_DEFAULTS, get_manager
from ...chat.providers import list_providers

router = APIRouter(prefix="/ai_chat", tags=["fxbot-ai-providers"], dependencies=[Depends(bearer_auth)])

_CAPABILITIES = {"chat", "embedding"}
_NAME_PATTERN = re.compile(r"[\w.-]{1,64}")
_DEFAULT_TIMEOUT = 60
_DEFAULT_EMBEDDING_DIM = 1536


def _ok(data: Any = None) -> dict[str, Any]:
    """返回成功响应。"""
    result: dict[str, Any] = {"success": True}
    if data is not None:
        result["data"] = data
    return result


def _fail(message: str) -> dict[str, Any]:
    """返回操作失败响应。"""
    return {"success": False, "message": message}


def _capability_of(meta: Any) -> str:
    """读取注册元数据中的能力类型。"""
    provider_type = getattr(meta, "provider_type", "")
    return str(getattr(provider_type, "value", provider_type) or "")


def _api_type_of(full_type: str, capability: str) -> str:
    """从注册名还原接口类型，约定为 f"{接口}_{能力}"。"""
    suffix = f"_{capability}"
    return full_type[: -len(suffix)] if full_type.endswith(suffix) else full_type


def _type_options() -> list[dict[str, Any]]:
    """返回可用的接口类型组合。"""
    options: list[dict[str, Any]] = []
    for full_type, meta in sorted(list_providers().items()):
        capability = _capability_of(meta)
        if capability not in _CAPABILITIES:
            continue
        defaults = meta.default_config or {}
        options.append(
            {
                "type": _api_type_of(full_type, capability),
                "provider_type": capability,
                "label": meta.desc or meta.display_name or full_type,
                "default_base_url": str(defaults.get("base_url") or ""),
                "default_timeout": defaults.get("timeout", _DEFAULT_TIMEOUT),
                "default_embedding_dim": defaults.get("embedding_dim"),
            }
        )
    return options


def _type_labels() -> dict[tuple[str, str], str]:
    """返回接口类型到展示名的映射。"""
    return {(str(item["type"]), str(item["provider_type"])): str(item["label"]) for item in _type_options()}


def _chat_config() -> dict[str, Any]:
    """读取 AI 对话配置。"""
    return get_manager().get_system()["chat"]


def _provider_configs(chat: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """提取合法的 Provider 配置映射。"""
    raw = chat.get("providers")
    if not isinstance(raw, dict):
        return {}
    return {str(name): config for name, config in raw.items() if isinstance(config, dict)}


def _save_chat(chat: dict[str, Any]) -> None:
    """保存 AI 对话配置。"""
    system = get_manager().get_system()
    system["chat"] = chat
    get_manager().register("system", SYSTEM_DEFAULTS).save(system)


def _first_chat_provider(configs: dict[str, dict[str, Any]]) -> str:
    """返回第一个对话 Provider 名称。"""
    for name in sorted(configs):
        if str(configs[name].get("provider_type") or "") == "chat":
            return name
    return ""


def _parse_name(raw: Any) -> str:
    """校验 Provider 名称。"""
    name = str(raw or "").strip()
    if not name:
        raise ValueError("请输入 Provider 名称")
    if _NAME_PATTERN.fullmatch(name) is None:
        raise ValueError("名称支持中英文、数字、下划线、点和短横线（1-64 字符）")
    return name


def _parse_float(raw: Any, label: str, *, maximum: float) -> float:
    """解析并校验正数。"""
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label}格式不正确") from exc
    if not 0 < value <= maximum:
        raise ValueError(f"{label}需在 0-{maximum:g} 之间")
    return value


def _parse_provider(payload: dict[str, Any], *, existing: dict[str, Any] | None) -> dict[str, Any]:
    """校验并构造 Provider 配置；更新时保留未识别的扩展字段。"""
    capability = str(payload.get("provider_type") or "").strip()
    if capability not in _CAPABILITIES:
        raise ValueError("请选择 Provider 能力")

    api_type = str(payload.get("type") or "").strip()
    option = next(
        (item for item in _type_options() if item["type"] == api_type and item["provider_type"] == capability),
        None,
    )
    if option is None:
        raise ValueError("请选择可用的接口类型")

    base_url = str(payload.get("base_url") or option["default_base_url"] or "").strip().rstrip("/")
    if not base_url:
        raise ValueError("请输入 Base URL")
    if len(base_url) > 500:
        raise ValueError("Base URL 过长")
    if re.match(r"^https?://", base_url, re.IGNORECASE) is None:
        raise ValueError("Base URL 需以 http:// 或 https:// 开头")

    model = str(payload.get("model") or "").strip()
    if not model:
        raise ValueError("请输入模型名称")
    if len(model) > 200:
        raise ValueError("模型名称过长")

    raw_timeout = payload.get("timeout")
    if raw_timeout in (None, ""):
        raw_timeout = option["default_timeout"] or _DEFAULT_TIMEOUT
    timeout = _parse_float(raw_timeout, "超时时间", maximum=600)
    config: dict[str, Any] = {
        "type": api_type,
        "provider_type": capability,
        "base_url": base_url,
        "model": model,
        "timeout": timeout,
    }

    if capability == "embedding":
        raw_dim = payload.get("embedding_dim")
        if raw_dim in (None, ""):
            raw_dim = option["default_embedding_dim"] or _DEFAULT_EMBEDDING_DIM
        try:
            embedding_dim = int(raw_dim)
        except (TypeError, ValueError) as exc:
            raise ValueError("向量维度格式不正确") from exc
        if not 1 <= embedding_dim <= 65536:
            raise ValueError("向量维度需在 1-65536 之间")
        config["embedding_dim"] = embedding_dim
    else:
        config.pop("embedding_dim", None)

    api_key = str(payload.get("api_key") or "").strip()
    if api_key:
        config["api_key"] = api_key
    else:
        config["api_key"] = str((existing or {}).get("api_key") or "").strip()
        if not config["api_key"]:
            raise ValueError("请输入 API Key")

    if existing is None:
        return config
    merged = {**existing, **config}
    if capability != "embedding":
        merged.pop("embedding_dim", None)
    return merged


@router.get("/providers")
async def get_ai_providers() -> dict[str, Any]:
    """列出 Provider 配置和可用接口类型。"""
    chat = _chat_config()
    configs = _provider_configs(chat)
    default_name = str(chat.get("provider") or "")
    labels = _type_labels()
    providers: list[dict[str, Any]] = []
    for name in sorted(configs):
        config = configs[name]
        api_type = str(config.get("type") or "")
        capability = str(config.get("provider_type") or "")
        providers.append(
            {
                "name": name,
                "type": api_type,
                "type_label": labels.get((api_type, capability), api_type),
                "provider_type": capability,
                "base_url": str(config.get("base_url") or ""),
                "model": str(config.get("model") or ""),
                "timeout": config.get("timeout", _DEFAULT_TIMEOUT),
                "embedding_dim": config.get("embedding_dim"),
                "has_api_key": bool(str(config.get("api_key") or "").strip()),
                "is_default": name == default_name,
            }
        )
    return _ok(
        {
            "providers": providers,
            "types": _type_options(),
            "default_provider": default_name,
        }
    )


@router.post("/provider")
async def create_ai_provider(payload: dict[str, Any]) -> dict[str, Any]:
    """新增 Provider。"""
    try:
        name = _parse_name(payload.get("name"))
    except ValueError as exc:
        return _fail(str(exc))

    chat = _chat_config()
    configs = _provider_configs(chat)
    if name in configs:
        return _fail("已存在同名 Provider")
    try:
        config = _parse_provider(payload, existing=None)
    except ValueError as exc:
        return _fail(str(exc))

    configs[name] = config
    chat["providers"] = configs
    if not str(chat.get("provider") or "").strip() and config["provider_type"] == "chat":
        chat["provider"] = name
    try:
        _save_chat(chat)
    except Exception:
        logger.opt(exception=True).error(f"[Console] 新增 Provider 失败: name={name}")
        return _fail("保存失败，请稍后再试")
    return _ok()


@router.put("/provider/{name}")
async def update_ai_provider(name: str, payload: dict[str, Any]) -> dict[str, Any]:
    """更新 Provider，名称不可修改。"""
    chat = _chat_config()
    configs = _provider_configs(chat)
    existing = configs.get(name)
    if existing is None:
        return _fail("Provider 不存在")
    try:
        config = _parse_provider(payload, existing=existing)
    except ValueError as exc:
        return _fail(str(exc))

    configs[name] = config
    chat["providers"] = configs
    if str(chat.get("provider") or "") == name and config["provider_type"] != "chat":
        chat["provider"] = _first_chat_provider(configs)
    try:
        _save_chat(chat)
    except Exception:
        logger.opt(exception=True).error(f"[Console] 更新 Provider 失败: name={name}")
        return _fail("保存失败，请稍后再试")
    return _ok()


@router.delete("/provider/{name}")
async def delete_ai_provider(name: str) -> dict[str, Any]:
    """删除 Provider；若它正被设为默认则自动切换。"""
    chat = _chat_config()
    configs = _provider_configs(chat)
    if name not in configs:
        return _fail("Provider 不存在")

    configs.pop(name, None)
    chat["providers"] = configs
    if str(chat.get("provider") or "") == name:
        chat["provider"] = _first_chat_provider(configs)
    try:
        _save_chat(chat)
    except Exception:
        logger.opt(exception=True).error(f"[Console] 删除 Provider 失败: name={name}")
        return _fail("删除失败，请稍后再试")
    return _ok()


@router.post("/provider/{name}/default")
async def set_default_ai_provider(name: str) -> dict[str, Any]:
    """把对话 Provider 设为默认。"""
    chat = _chat_config()
    configs = _provider_configs(chat)
    config = configs.get(name)
    if config is None:
        return _fail("Provider 不存在")
    if str(config.get("provider_type") or "") != "chat":
        return _fail("只有对话 Provider 可以设为默认")

    chat["provider"] = name
    try:
        _save_chat(chat)
    except Exception:
        logger.opt(exception=True).error(f"[Console] 设置默认 Provider 失败: name={name}")
        return _fail("保存失败，请稍后再试")
    return _ok()
