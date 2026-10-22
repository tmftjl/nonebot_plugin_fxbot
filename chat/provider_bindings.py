"""会话级 Provider 绑定。"""

from __future__ import annotations

import os
import json
from pathlib import Path

from nonebot import logger

from ..utils.paths import config_dir


def _bindings_path() -> Path:
    """返回会话 Provider 绑定的存储路径。"""
    return config_dir("ai_chat") / "provider_bindings.json"


def load_provider_bindings() -> dict[str, str]:
    """读取会话到 Provider 的绑定。"""
    try:
        raw = json.loads(_bindings_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception:
        logger.opt(exception=True).warning("[AI Chat] 读取 Provider 绑定失败")
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(key): str(value).strip() for key, value in raw.items() if str(value).strip()}


def get_bound_provider(session_id: str) -> str:
    """读取会话绑定的 Provider 名。"""
    return load_provider_bindings().get(str(session_id), "")


def set_bound_provider(session_id: str, name: str | None) -> None:
    """写入或清除会话绑定的 Provider 名。"""
    bindings = load_provider_bindings()
    key = str(session_id)
    if name:
        bindings[key] = str(name)
    else:
        bindings.pop(key, None)
    path = _bindings_path()
    temp = path.with_name(f"{path.name}.tmp")
    temp.write_text(json.dumps(bindings, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)
