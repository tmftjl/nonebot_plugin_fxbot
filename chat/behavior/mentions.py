"""回复开头的 @ 解析。

MaiBot 通过 reply 工具的 `attach_at` 参数实现（commit 062d45c，`src/maisaka/builtin_tool/reply.py`）；
FxBot 没有回复工具，改为解析模型写在开头的 `@昵称`。
"""

from __future__ import annotations

import re

from ..types import ChatRequest
from ...config import get_manager as get_config_manager

_MAX_MENTIONS = 3
_LEADING = re.compile(r"^\s*@([^\s@]+)")


def reply_at_enabled() -> bool:
    """回复时是否允许 @ 人。"""
    try:
        behavior = get_config_manager().get_system()["chat"].get("behavior") or {}
    except Exception:
        return True
    value = behavior.get("enable_reply_at", True)
    return value if isinstance(value, bool) else True


def mention_targets(request: ChatRequest) -> dict[str, str]:
    """可被 @ 的人：本条消息的发送者与消息中被 @ 的成员。"""
    targets: dict[str, str] = {}
    if request.user_id and request.user_name:
        targets[request.user_name.casefold()] = request.user_id
    for ref in request.mentions:
        if ref.name and ref.user_id and ref.user_id != "all":
            targets.setdefault(ref.name.casefold(), ref.user_id)
    return targets


def split_mentions(text: str, request: ChatRequest) -> tuple[list[str], str]:
    """解析开头连续的 @昵称，返回用户 ID 与剩余正文；解析不了的原样保留。"""
    if not text or not request.is_group or not reply_at_enabled():
        return [], text
    targets = mention_targets(request)
    user_ids: list[str] = []
    rest = text
    while len(user_ids) < _MAX_MENTIONS:
        match = _LEADING.match(rest)
        if match is None:
            break
        user_id = targets.get(match.group(1).casefold())
        if user_id is None or user_id in user_ids:
            break
        user_ids.append(user_id)
        rest = rest[match.end() :]
    if not user_ids or not rest.strip():
        return [], text
    return user_ids, rest.lstrip()
