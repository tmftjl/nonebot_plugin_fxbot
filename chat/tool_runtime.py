"""平台能力注入用 ToolRuntimeFactory。"""

from __future__ import annotations

from typing import Any

from .tools import ToolRuntime


class ToolRuntimeFactory:
    """创建工具运行时。"""

    def create(
        self, *, bot: Any = None, event: Any = None, matcher: Any = None
    ) -> ToolRuntime:
        """从 NoneBot 对象创建 ToolRuntime。"""
        return ToolRuntime(bot=bot, event=event, matcher=matcher)


default_runtime_factory = ToolRuntimeFactory()
