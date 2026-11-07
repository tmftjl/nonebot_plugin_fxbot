"""通过独立系统段注入原版提示词，不改人格解析、历史或权限。"""

from __future__ import annotations

from pathlib import Path

from .config import is_enabled
from ...chat.hooks import ChatHookContext, SystemPromptSection, register_chat_hook

PROMPT_TEXT = (Path(__file__).parents[2] / "resources" / "infinite_gen2.prompt").read_text(encoding="utf-8").strip()


def prompt_section(context: ChatHookContext) -> SystemPromptSection | None:
    """普通规划、共享规划和回复均按原版顺序贡献独立系统段。"""
    if context.phase not in {"reply", "planner", "focus_planner"} or not is_enabled():
        return None
    return SystemPromptSection(text=PROMPT_TEXT, order=100)


def install() -> None:
    """同名注册保证重复加载不叠加提示词。"""
    register_chat_hook("system_prompt", "infinite_gen2", prompt_section)
