"""读取机器人自身群角色，仅供对话背景使用，不参与工具授权。"""

from __future__ import annotations

from .types import ChatRequest

ROLE_LABELS = {"owner": "群主", "admin": "管理员", "member": "普通成员"}


def group_identity_prompt(request: ChatRequest) -> str:
    """明确绑定身份所属群，避免与发言人或其他共享群的身份混淆。"""
    if not request.is_group:
        return ""
    label = ROLE_LABELS.get(request.metadata.get("bot_group_role"))
    if label is None:
        return ""
    return (
        f"[你的群身份]\n你在群 {request.group_id} 的身份：{label}。"
        "仅指机器人自己，非发言人；仅本群有效，不替代工具授权或平台能力限制。"
    )
