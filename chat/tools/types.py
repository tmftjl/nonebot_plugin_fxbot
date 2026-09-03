"""ToolContext、ToolSpec 和 ToolError。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

try:
    from pydantic import BaseModel
except ImportError:  # pragma: no cover
    BaseModel = object  # type: ignore

ToolHandler = Callable[..., str | dict[str, Any] | Any | Awaitable[Any]]


@dataclass
class ToolContext:
    """工具执行上下文，保持与 NoneBot 解耦。"""

    user_id: str = ""
    group_id: str | None = None
    session_id: str = ""
    platform: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_group(self) -> bool:
        """是否为群聊。"""
        return bool(self.group_id)


class ToolError(Exception):
    """可安全返回给模型的工具错误。"""

    def __init__(self, message: str, *, code: str = "tool_error", data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.data = data


@dataclass(frozen=True)
class ToolSpec:
    """工具描述和处理器。"""

    name: str
    description: str
    parameters: dict[str, Any]
    handler: ToolHandler
    args_model: type[BaseModel] | None = None  # type: ignore[name-defined]
