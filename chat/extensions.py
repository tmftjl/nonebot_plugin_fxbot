"""业务插件使用的稳定聊天扩展入口，不需要导入 Focus 或发送器内部对象。"""

from __future__ import annotations

import asyncio
from uuid import uuid4
from typing import Any
from collections.abc import Callable, Collection

from .audit import AuditObserver
from .hooks import (
    HookKind,
    ChatHookError,
    ChatHookContext,
    PersonaOverride,
    SystemPromptSection,
    register_chat_hook,
)
from .stages import CHAT_STAGES, StageName
from .continuation import reply_scheduler
from .session_hooks import SessionPolicy

__all__ = [
    "CHAT_STAGES",
    "ChatExtension",
    "ChatHookContext",
    "ChatHookError",
    "HookKind",
    "PersonaOverride",
    "SessionPolicy",
    "StageName",
    "SystemPromptSection",
    "register_chat_hook",
]


class ChatExtension:
    """将一个插件的 Hook 和后台对话任务归为同一生命周期。"""

    def __init__(self, name: str) -> None:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("聊天扩展名称不能为空")
        self.name = name
        self._task_owner = f"{name}@{uuid4().hex}"
        self._removers: dict[tuple[str, str], Callable[[], None]] = {}
        self._closed = False

    def on(
        self,
        kind: HookKind,
        callback: Callable[..., Any],
        *,
        key: str = "default",
        priority: int = 100,
        events: Collection[str] | None = None,
        background: bool = False,
        queue_size: int = 128,
    ) -> Callable[[], None]:
        """统一注册会话、守卫、过滤器和通知；返回单项注销句柄。"""
        if self._closed:
            raise RuntimeError("聊天扩展已关闭")
        if not isinstance(key, str) or not key.strip():
            raise ValueError("扩展注册项名称不能为空")
        if type(background) is not bool or (background and kind != "observer"):
            raise ValueError("只有通知可选择后台执行")
        if not callable(callback):
            raise ValueError("扩展回调必须可调用")
        audit = AuditObserver(self.name, callback, queue_size=queue_size) if background else None
        slot = (kind, key)
        # 用长度前缀避免插件名称与 key 组合产生碰撞。
        try:
            unregister = register_chat_hook(
                kind,
                f"{len(self.name)}:{self.name}:{key}",
                audit if audit is not None else callback,
                priority=priority,
                events=events,
            )
        except Exception:
            if audit is not None:
                audit.close()
            raise

        def remove() -> None:
            """旧句柄只清理旧订阅，替换后不会误删当前订阅。"""
            unregister()
            if audit is not None:
                audit.close()
            if self._removers.get(slot) is remove:
                self._removers.pop(slot)

        previous = self._removers.get(slot)
        if previous is not None:
            previous()
        self._removers[slot] = remove
        return remove

    def schedule_reply(
        self, context: ChatHookContext, instruction: str, *, delay: float = 0, key: str = "default"
    ) -> asyncio.Task[str]:
        """基于通知中的真实会话安排后续回复，不接受任意 Bot 或目标 ID。"""
        if self._closed:
            raise RuntimeError("聊天扩展已关闭")
        return reply_scheduler.schedule(self._task_owner, key, context, instruction, delay=delay, source=self.name)

    def close(self) -> None:
        """同步撤销全部注册与后台任务，重复调用安全。"""
        if self._closed:
            return
        self._closed = True
        for remove in reversed(tuple(self._removers.values())):
            remove()
        self._removers.clear()
        reply_scheduler.cancel(owner=self._task_owner)
