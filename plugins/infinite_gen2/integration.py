"""通过独立系统段注入原版提示词，不改人格解析、历史或权限。"""

from __future__ import annotations

from pathlib import Path

from .config import is_enabled, direct_llm_enabled
from ...chat.hooks import ChatHookContext, SystemPromptSection, register_chat_hook
from ...chat.session_hooks import SessionPolicy, register_session_policy

PROMPT_TEXT = (Path(__file__).parents[2] / "resources" / "infinite_gen2.prompt").read_text(encoding="utf-8").strip()


def prompt_section(context: ChatHookContext) -> SystemPromptSection | None:
    """普通规划、共享规划、普通回复和直聊均贡献独立系统段。"""
    if context.phase not in {"reply", "direct_reply", "planner", "focus_planner"} or not is_enabled():
        return None
    return SystemPromptSection(text=PROMPT_TEXT, order=100)


def direct_chat_policy(session_id: str) -> SessionPolicy | None:
    """开关打开时声明纯人格直聊，核心按会话策略统一处理。"""
    del session_id
    return SessionPolicy(direct_reply=True) if direct_llm_enabled() else None


def install() -> None:
    """同名注册保证重复加载不叠加提示词。"""
    register_chat_hook("system_prompt", "infinite_gen2", prompt_section)
    register_session_policy("infinite_gen2", direct_chat_policy)
