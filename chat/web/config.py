"""从系统配置组装联网运行时快照。"""

from __future__ import annotations

from typing import Any

from .service import WebRuntimeConfig
from ...config import get_manager
from .adapters import WebServiceConfig

_FIELDS = (
    "adapter",
    "base_url",
    "api_key",
    "timeout",
    "search_depth",
    "extract_depth",
)


def _service_config(entry: dict[str, Any]) -> WebServiceConfig:
    values = {key: entry.get(key) for key in _FIELDS if entry.get(key) is not None}
    return WebServiceConfig(name=str(entry.get("name") or ""), **values)


def web_runtime() -> WebRuntimeConfig:
    """读取 web.services 与 model_routing.web_search；配置异常时按未启用处理。"""
    try:
        chat = get_manager().get_system()["chat"]
    except Exception:
        return WebRuntimeConfig()
    web = chat.get("web") or {}
    if not isinstance(web, dict):
        return WebRuntimeConfig()
    services: dict[str, WebServiceConfig] = {}
    for entry in web.get("services") or []:
        if not isinstance(entry, dict) or not str(entry.get("name") or "").strip():
            continue
        config = _service_config(entry)
        services[config.name] = config
    selected = str((chat.get("model_routing") or {}).get("web_search") or "").strip()
    preview_chars = web.get("preview_chars", 4000)
    if isinstance(preview_chars, bool) or not isinstance(preview_chars, int):
        preview_chars = 4000
    return WebRuntimeConfig(preview_chars=preview_chars, services=services, selected=selected)
