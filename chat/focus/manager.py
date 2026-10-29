"""Focus 共享组的配置解析与运行时归属。"""

from __future__ import annotations

import json
import asyncio
from typing import Any
from hashlib import sha256

from nonebot import logger

from .context import FocusCheckpointStore
from ...config import get_manager as get_config_manager
from .group_runtime import FocusMember, FocusGroupRuntime

_DEFAULTS: dict[str, Any] = {"enabled": False, "on_private": False, "chat_whitelist": [], "groups": []}
FAILURE_THRESHOLD = 3


def focus_config() -> dict[str, Any]:
    """读取 chat.focus；缺失或损坏时按关闭处理。"""
    try:
        value = get_config_manager().get_system()["chat"].get("focus")
    except Exception:
        return dict(_DEFAULTS)
    if not isinstance(value, dict):
        return dict(_DEFAULTS)
    return {**_DEFAULTS, **value}


def _target_matches(target: Any, request: Any) -> bool:
    """平台、会话类型与 ID 全部一致才算命中。"""
    if not isinstance(target, dict) or str(target.get("platform") or "") != request.platform:
        return False
    if target.get("type") != ("group" if request.is_group else "private"):
        return False
    item_id = request.group_id if request.is_group else request.user_id
    return str(target.get("item_id") or "") == str(item_id or "")


def _scope_key(request: Any) -> str:
    """组身份基于规则内容而非列表顺序；不在任何组内时各聊天独立。"""
    groups = focus_config()["groups"] or []
    if not groups:
        return "session:" + request.session_id
    for group in groups:
        targets = group.get("targets") if isinstance(group, dict) else None
        if not isinstance(targets, list) or not any(_target_matches(target, request) for target in targets):
            continue
        rules = [
            json.dumps(target, sort_keys=True, ensure_ascii=False) for target in targets if isinstance(target, dict)
        ]
        canonical = json.dumps(sorted(rules), sort_keys=True, ensure_ascii=False)
        return "group:" + sha256(canonical.encode("utf-8")).hexdigest()[:24]
    return "session:" + request.session_id


class FocusModeManager:
    """按共享组维护唯一的共享规划器；配置变化在消息边界惰性生效。"""

    def __init__(self) -> None:
        self.groups: dict[str, FocusGroupRuntime] = {}
        self._scope_by_session: dict[str, str] = {}
        self._requests: dict[str, Any] = {}
        self._lock = asyncio.Lock()
        self._stopping = False

    def enabled(self) -> bool:
        return bool(focus_config()["enabled"])

    def status_snapshot(self) -> list[dict[str, Any]]:
        return [
            {
                "scope": group.scope_id,
                "sessions": list(group.members),
                "worker_alive": group._worker is not None and not group._worker.done(),
                "consecutive_failures": group._failures,
                "waiting": group._wait_until is not None,
                "checkpoint_path": str(group._store.path),
            }
            for group in self.groups.values()
        ]

    def attached(self, session_id: str) -> bool:
        return session_id in self._scope_by_session

    def is_enabled_for_request(self, request: Any) -> bool:
        """Focus 是否接管这条消息所在的聊天。"""
        cfg = focus_config()
        if not cfg["enabled"]:
            return False
        if not request.is_group and not cfg["on_private"]:
            return False
        whitelist = cfg["chat_whitelist"] or []
        return not whitelist or any(_target_matches(target, request) for target in whitelist)

    def is_enabled_for_session(self, session_id: str) -> bool:
        request = self._requests.get(session_id)
        return request is not None and self.is_enabled_for_request(request)

    def scope_key(self, session_id: str) -> str:
        request = self._requests.get(session_id)
        return _scope_key(request) if request is not None else ""

    async def register(self, member: FocusMember, request: Any, *, is_at: bool) -> None:
        """登记入站消息；归属变化时先退出旧组再接入新组。"""
        session_id = request.session_id
        scope = _scope_key(request)
        async with self._lock:
            if self._stopping:
                return
            self._requests[session_id] = request
            previous = self._scope_by_session.get(session_id)
            if previous is not None and previous != scope:
                await self._detach_locked(session_id, clear_context=False)
            group = self.groups.get(scope)
            if group is None:
                group = FocusGroupRuntime(scope)
                self.groups[scope] = group
                await group.restore()
            group.add_member(member)
            self._scope_by_session[session_id] = scope
            await group.register(member, request, is_at=is_at)

    async def detach(self, session_id: str, *, clear_context: bool = False) -> None:
        """让该聊天退回普通对话；默认保留它留下的共享上下文。"""
        async with self._lock:
            self._requests.pop(session_id, None)
            await self._detach_locked(session_id, clear_context=clear_context)

    async def stop_all(self) -> None:
        """停止全部共享组并保存检查点；关闭后不再接收入站消息。"""
        async with self._lock:
            self._stopping = True
            groups = list(self.groups.values())
            self.groups.clear()
            self._scope_by_session.clear()
            self._requests.clear()
            results = await asyncio.gather(*(group.stop() for group in groups), return_exceptions=True)
            for group, result in zip(groups, results):
                if isinstance(result, BaseException):
                    logger.opt(exception=result).warning(f"[ChatFocus] 共享组关闭失败: {group.scope_id}")

    async def discard_message(self, session_id: str, message_id: str) -> bool:
        """撤回消息：从所有共享组的时间线里去掉它。"""
        async with self._lock:
            removed = False
            for group in list(self.groups.values()):
                removed = await group.discard_message(session_id, message_id) or removed
            return removed

    async def clear_context(self, session_id: str) -> None:
        """清空聊天时把涉及它的共享上下文一并丢弃。"""
        async with self._lock:
            if self.attached(session_id):
                await self._detach_locked(session_id, clear_context=True)
            for group in list(self.groups.values()):
                if any(session_id in entry.session_ids for entry in group.entries):
                    await group.purge_session(session_id)
            await asyncio.to_thread(FocusCheckpointStore.purge_unloaded_session, session_id, set(self.groups))

    async def _detach_locked(self, session_id: str, *, clear_context: bool) -> None:
        scope = self._scope_by_session.pop(session_id, None)
        if scope is None:
            return
        group = self.groups.get(scope)
        if group is None:
            return
        await group.remove_member(session_id, clear_context=clear_context)
        if not group.members:
            await group.stop()
            self.groups.pop(scope, None)


focus_mode_manager = FocusModeManager()
