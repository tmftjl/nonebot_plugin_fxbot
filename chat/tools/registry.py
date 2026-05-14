"""ToolRegistry 和 @tool 装饰器。"""

from __future__ import annotations

from typing import Any

from .types import ToolHandler, ToolSpec


class ToolRegistry:
    """工具注册表。"""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> ToolSpec:
        """注册工具。"""
        self._tools[spec.name] = spec
        return spec

    def get(self, name: str) -> ToolSpec | None:
        """获取工具。"""
        return self._tools.get(name)

    def list(self) -> list[ToolSpec]:
        """列出工具。"""
        return list(self._tools.values())

    def to_openai_tools(self) -> list[dict[str, Any]]:
        """转换为 OpenAI function calling 工具格式。"""
        return [
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


default_registry = ToolRegistry()


def tool(
    *,
    name: str,
    description: str,
    parameters: dict[str, Any],
    args_model: Any = None,
    registry: ToolRegistry = default_registry,
):
    """注册 AI 工具的装饰器。"""

    def decorator(func: ToolHandler) -> ToolHandler:
        registry.register(
            ToolSpec(
                name=name,
                description=description,
                parameters=parameters,
                handler=func,
                args_model=args_model,
            )
        )
        return func

    return decorator
