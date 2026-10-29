"""按会话记录 tool_search 已发现的延迟工具。"""

from __future__ import annotations

import json
from typing import Any, Iterable
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


def sync_discovered(session_id: str, messages: list[dict[str, Any]]) -> None:
    """只保留当前上下文中有完整检索调用和成功结果支撑的工具发现。"""
    from .registry import default_registry

    calls = {
        call.get("id")
        for message in messages
        for call in message.get("tool_calls") or []
        if (call.get("function") or {}).get("name") == "tool_search" and call.get("id")
    }
    names = set()
    for message in messages:
        if message.get("role") != "tool" or message.get("tool_call_id") not in calls:
            continue
        try:
            result = json.loads(message.get("content") or "{}")
        except (TypeError, ValueError):
            continue
        if not isinstance(result, dict) or not result.get("ok"):
            continue
        for line in str(result.get("result") or "").splitlines():
            if not line.startswith("- "):
                continue
            name = line[2:].split("：", 1)[0].strip()
            spec = default_registry.get(name)
            if spec is not None and spec.deferred:
                names.add(name)
    _discovered.pop(session_id, None)
    if names:
        discover(session_id, names)


def deferred_reminder(allowed_names: set[str], session_id: str) -> str:
    """使用上游提示格式列出当前会话尚未发现的可用工具。"""
    from .registry import default_registry

    specs = [
        spec
        for spec in default_registry.list()
        if spec.name in allowed_names and spec.deferred and not tool_visible(spec, session_id)
    ]
    if not specs:
        return ""
    lines = [f"{index}. {spec.name}: {spec.description.strip()}" for index, spec in enumerate(specs, 1)]
    return "\n".join(
        [
            "<system-reminder>",
            "以下工具当前未直接暴露给你，但可以通过 tool_search 工具发现并在后续轮次中使用：",
            *lines,
            "",
            "如需其中某个工具，请先调用 tool_search。tool_search 只负责发现工具，不直接执行。",
            "</system-reminder>",
        ]
    )
