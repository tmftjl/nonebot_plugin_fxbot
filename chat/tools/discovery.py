"""按会话记录 tool_search 已发现的延迟工具。"""

from __future__ import annotations

from typing import Iterable
from collections import OrderedDict

from .types import ToolSpec

_MAX_SESSIONS = 512
_discovered: OrderedDict[str, set[str]] = OrderedDict()


def discovered(session_id: str) -> set[str]:
    """返回该会话已发现的工具名。"""
    return set(_discovered.get(session_id) or ())


def discover(session_id: str, names: Iterable[str]) -> list[str]:
    """记录新发现的工具名，返回本次新增的部分。"""
    if not session_id:
        return []
    known = _discovered.setdefault(session_id, set())
    _discovered.move_to_end(session_id)
    while len(_discovered) > _MAX_SESSIONS:
        _discovered.popitem(last=False)
    fresh = [name for name in names if name and name not in known]
    known.update(fresh)
    return fresh


def tool_visible(spec: ToolSpec, session_id: str) -> bool:
    """延迟工具只有被 tool_search 发现后才把定义发给模型。"""
    return not getattr(spec, "deferred", False) or spec.name in (_discovered.get(session_id) or ())
