"""工具原始结果的内存暂存；超长结果写入上下文前只保留预览与编号。"""

from __future__ import annotations

from typing import Optional
from datetime import datetime
from collections import OrderedDict
from dataclasses import dataclass

from ...utils import tz

MAX_STORED_RESULTS_PER_SESSION = 20


@dataclass(frozen=True, slots=True)
class StoredToolResult:
    """一条暂存的工具原始结果。"""

    result_id: str
    tool_name: str
    content: str
    created_at: datetime


class ToolResultStore:
    """按会话分组暂存工具原始结果，超出后丢弃最早的。"""

    def __init__(self) -> None:
        self._results: dict[str, OrderedDict[str, StoredToolResult]] = {}
        self._next_index: dict[str, int] = {}

    def save(self, session_id: str, tool_name: str, content: str) -> str:
        """暂存原始结果并返回会话内唯一编号，如 ``r3``。"""
        next_index = self._next_index.get(session_id, 0) + 1
        self._next_index[session_id] = next_index
        result_id = f"r{next_index}"
        session_results = self._results.setdefault(session_id, OrderedDict())
        session_results[result_id] = StoredToolResult(
            result_id=result_id,
            tool_name=tool_name,
            content=content,
            created_at=tz.now(),
        )
        while len(session_results) > MAX_STORED_RESULTS_PER_SESSION:
            session_results.popitem(last=False)
        return result_id

    def clear(self, session_id: str) -> None:
        """丢弃指定会话的暂存结果，保留编号计数。"""
        self._results.pop(session_id, None)

    def get(self, session_id: str, result_id: str) -> Optional[StoredToolResult]:
        """按编号取回原始结果；已淘汰或进程重启后返回 None。"""
        return self._results.get(session_id, OrderedDict()).get(result_id.strip())


tool_result_store = ToolResultStore()
