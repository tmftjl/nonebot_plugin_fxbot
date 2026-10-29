"""聊天流桥接。

FxBot 的 ``session_id`` 同时就是租户标识，格式为
``{platform}:{self_id}:group:{group_id}`` 或 ``{platform}:{self_id}:private:{user_id}``。
A_Memorix 的聊天过滤只需要群号与用户号，这里直接从租户串解析。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class _SessionView:
    session_id: str
    group_id: str = ""
    user_id: str = ""


class _ChatManagerBridge:
    @staticmethod
    def get_existing_session_by_session_id(session_id: str) -> Optional[_SessionView]:
        token = str(session_id or "").strip()
        if not token:
            return None
        if ":group:" in token:
            group_id = token.rsplit(":group:", 1)[1].strip()
            return _SessionView(session_id=token, group_id=group_id) if group_id else None
        if ":private:" in token:
            user_id = token.rsplit(":private:", 1)[1].strip()
            return _SessionView(session_id=token, user_id=user_id) if user_id else None
        return None


chat_manager = _ChatManagerBridge()
