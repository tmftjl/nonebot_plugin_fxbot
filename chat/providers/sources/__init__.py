"""Provider source 安全导入入口。"""

from __future__ import annotations

from importlib import import_module

from nonebot import logger

_SOURCE_MODULES = ("openai", "anthropic", "gemini", "vertex")


def import_provider_sources() -> dict[str, Exception]:
    """逐个导入 Provider source，失败时只禁用对应 source。"""
    failures: dict[str, Exception] = {}
    package = __name__
    for module_name in _SOURCE_MODULES:
        try:
            import_module(f"{package}.{module_name}")
        except Exception as exc:
            failures[module_name] = exc
            logger.warning(f"[Provider] {module_name} source 导入失败，已跳过: {exc}")
    return failures


SOURCE_IMPORT_FAILURES = import_provider_sources()

__all__ = ["SOURCE_IMPORT_FAILURES", "import_provider_sources"]
