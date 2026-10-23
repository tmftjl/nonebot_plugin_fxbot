"""会话绑定存储：人格、Provider 等各类绑定按会话共用一个文件。"""

from __future__ import annotations

import os
import json
from typing import Any
from pathlib import Path

from nonebot import logger

from ..utils.paths import config_dir

PERSONA = "persona"
PROVIDER = "provider"


def _bindings_path() -> Path:
    """返回会话绑定的存储路径。"""
    return config_dir("ai_chat") / "bindings.json"


def _clean(items: Any) -> dict[str, str]:
    """清理一个会话下的各类绑定。"""
    if not isinstance(items, dict):
        return {}
    return {str(kind): str(value).strip() for kind, value in items.items() if str(value).strip()}


def _save(bindings: dict[str, dict[str, str]]) -> None:
    """原子写入绑定文件。"""
    path = _bindings_path()
    temp = path.with_name(f"{path.name}.tmp")
    temp.write_text(json.dumps(bindings, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def _load() -> dict[str, dict[str, str]]:
    """读取全部绑定。"""
    try:
        raw = json.loads(_bindings_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception:
        logger.opt(exception=True).warning("[AI Chat] 读取会话绑定失败")
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(session_id): cleaned for session_id, items in raw.items() if (cleaned := _clean(items))}


def get_binding(kind: str, session_id: str) -> str:
    """读取会话在某一类绑定上的值。"""
    return _load().get(str(session_id), {}).get(kind, "")


def set_binding(kind: str, session_id: str, value: str | None) -> None:
    """写入或清除会话绑定；会话下没有任何绑定时从文件里移除。"""
    bindings = _load()
    key = str(session_id)
    values = bindings.setdefault(key, {})
    if value:
        values[kind] = str(value)
    else:
        values.pop(kind, None)
        if not values:
            bindings.pop(key, None)
    _save(bindings)
