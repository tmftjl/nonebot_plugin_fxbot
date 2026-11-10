"""模型调用所属的会话与轮次上下文，供请求快照落盘关联。"""

from __future__ import annotations

from contextvars import Token, ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class TurnContext:
    """当前正在处理的一轮对话。"""

    session_id: str
    turn_id: str
    related_sessions: tuple[str, ...] = ()


_current: ContextVar[TurnContext | None] = ContextVar("fxbot_turn_context", default=None)


def bind_turn(session_id: str, turn_id: str, *, related_sessions: tuple[str, ...] = ()) -> Token[TurnContext | None]:
    """绑定当前轮次，调用方负责在 finally 中复位。"""
    return _current.set(TurnContext(str(session_id or ""), str(turn_id or ""), related_sessions))


def unbind_turn(token: Token[TurnContext | None]) -> None:
    """恢复绑定前的上下文。"""
    _current.reset(token)


def current_turn() -> TurnContext | None:
    """返回当前轮次；后台任务里可能为空。"""
    return _current.get()
