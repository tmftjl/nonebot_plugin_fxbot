"""暴露给工具的运行时能力。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ToolRuntime:
    """工具运行时，封装平台对象。"""

    bot: Any = None
    event: Any = None
    matcher: Any = None

    def require_bot(self) -> Any:
        """获取 bot，不存在时抛错。"""
        if self.bot is None:
            raise RuntimeError("当前工具需要 bot 对象")
        return self.bot

    def require_event(self) -> Any:
        """获取 event，不存在时抛错。"""
        if self.event is None:
            raise RuntimeError("当前工具需要 event 对象")
        return self.event
