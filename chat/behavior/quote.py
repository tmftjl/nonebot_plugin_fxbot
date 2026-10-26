"""引用回复的触发规则。"""

from __future__ import annotations

from ..types import ChatRequest

QUOTE_MODES = frozenset({"auto", "always", "never"})


def normalize_quote_mode(value: object) -> str:
    """只接受三种模式，非法值回退 auto。"""
    mode = str(value or "").strip().lower()
    return mode if mode in QUOTE_MODES else "auto"


def should_quote(request: ChatRequest, *, mode: str = "auto", has_newer: bool = False) -> bool:
    """群聊按上下文决定是否引用触发消息；私聊默认不引用。"""
    normalized = normalize_quote_mode(mode)
    if normalized == "never" or not request.message_id:
        return False
    if normalized == "always":
        return True
    if not request.is_group:
        return False
    return bool(request.quoted_text) or has_newer or len(request.source_author_ids) > 1
