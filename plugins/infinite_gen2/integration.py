"""通过人格扩展追加原版提示词，不改历史、规划协议或权限。"""

from __future__ import annotations

from pathlib import Path

from .config import is_enabled
from ...chat.hooks import ChatHookContext, PersonaOverride, register_chat_hook

PROMPT_TEXT = (Path(__file__).parents[2] / "resources" / "infinite_gen2.prompt").read_text(encoding="utf-8").strip()


def extend_persona(context: ChatHookContext, current_name: str, current_text: str) -> PersonaOverride | None:
    """只在面向用户的回复中追加提示词，保留原人格名称与正文。"""
    if context.phase != "reply" or not is_enabled():
        return None
    return PersonaOverride(text=f"{current_text}\n\n{PROMPT_TEXT}".strip())


def install() -> None:
    """同名注册保证重复加载不叠加提示词。"""
    register_chat_hook("persona", "infinite_gen2", extend_persona)
