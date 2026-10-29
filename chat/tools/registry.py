"""ToolRegistry 和 @tool 装饰器。"""

from __future__ import annotations

from typing import Any

from .types import ToolSpec, ToolHandler


class ToolRegistry:
    """工具注册表。"""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}
        self._openai_tools_cache: list[dict[str, Any]] | None = None

    def register(self, spec: ToolSpec) -> ToolSpec:
        """注册工具。"""
        self._tools[spec.name] = spec
        self._openai_tools_cache = None
        return spec

    def unregister(self, name: str) -> None:
        """注销工具。"""
        if self._tools.pop(name, None) is not None:
            self._openai_tools_cache = None

    def get(self, name: str) -> ToolSpec | None:
        """获取工具。"""
        return self._tools.get(name)

    def list(self) -> list[ToolSpec]:
        """列出工具。"""
        return list(self._tools.values())

    def to_openai_tools(self) -> list[dict[str, Any]]:
        """转换为 OpenAI function calling 工具格式。"""
        if self._openai_tools_cache is None:
            self._openai_tools_cache = [
                {
                    "type": "function",
                    "function": {
                        "name": spec.name,
                        "description": spec.description,
                        "parameters": spec.parameters,
                    },
                }
                for spec in self.list()
            ]
        return self._openai_tools_cache


default_registry = ToolRegistry()


def tool(
    *,
    name: str,
    description: str,
    parameters: dict[str, Any],
    args_model: Any = None,
    permission: str = "",
    title: str = "",
    deferred: bool = False,
    registry: ToolRegistry = default_registry,
):
    """注册 AI 工具的装饰器；permission 复用命令权限，格式为 plugin:command。

    deferred 表示默认不把工具定义发给模型，由 tool_search 检索后按需启用。
    """

    def decorator(func: ToolHandler) -> ToolHandler:
        registry.register(
            ToolSpec(
                name=name,
                description=description,
                parameters=parameters,
                handler=func,
                args_model=args_model,
                permission=permission,
                title=title,
                deferred=deferred,
            )
        )
        return func

    return decorator
