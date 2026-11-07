"""聊天扩展点：只读快照、拒绝型守卫、明确返回值和失败隔离的生命周期通知。"""

from __future__ import annotations

import asyncio
import inspect
from copy import deepcopy
from math import isfinite
from types import MappingProxyType
from typing import Any
from dataclasses import dataclass
from collections.abc import Callable

from nonebot import logger

HOOK_TIMEOUT_SECONDS = 5.0
_hooks: dict[str, dict[str, Callable[..., Any]]] = {}
_KINDS = frozenset(
    {"request_guard", "persona", "system_prompt", "prompt", "tool_guard", "reply_text", "send_guard", "observer"}
)


def _snapshot(value: Any) -> Any:
    """递归冻结常见容器，通知不能通过嵌套参数回写执行状态。"""
    if isinstance(value, dict):
        return MappingProxyType({key: _snapshot(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_snapshot(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_snapshot(item) for item in value)
    return deepcopy(value)


@dataclass(frozen=True)
class ChatHookContext:
    """不可变的调用身份，不包含可写请求、事件、发送目标或运行时权限对象。"""

    session_id: str
    user_id: str = ""
    group_id: str | None = None
    platform: str = ""
    turn_id: str = ""
    phase: str = "reply"
    epoch: int | None = None


@dataclass(frozen=True)
class PersonaOverride:
    """只替换本次生成的人格名称或原始人格文本，不改会话绑定和真实场景。"""

    name: str | None = None
    text: str | None = None


@dataclass(frozen=True)
class SystemPromptSection:
    """人格解析后、阶段任务规则前的独立系统段，按数值及注册名排序。"""

    text: str
    order: float = 100


class ChatHookError(RuntimeError):
    """阻止有风险的回调失败继续生成或发送。"""


def hook_context(request: Any, *, phase: str | None = None) -> ChatHookContext:
    """从真实请求提取身份，不向插件传递可改写的原对象。"""
    return ChatHookContext(
        session_id=request.session_id,
        user_id=request.user_id,
        group_id=request.group_id,
        platform=request.platform,
        turn_id=request.turn_id,
        phase=phase
        or (
            "planner" if request.metadata.get("planner_loop") and not request.metadata.get("planner_reply") else "reply"
        ),
        epoch=request.accepted_epoch,
    )


def register_chat_hook(kind: str, name: str, callback: Callable[..., Any]) -> Callable[[], None]:
    """按类型和插件名称注册，稳定排序、同名替换并返回精确注销句柄。"""
    if kind not in _KINDS or not isinstance(name, str) or not name.strip() or not callable(callback):
        raise ValueError("聊天 hook 类型、名称或回调无效")
    if kind in {"persona", "system_prompt", "prompt", "reply_text"} and inspect.iscoroutinefunction(callback):
        raise ValueError("人格、提示和正文过滤器必须同步")
    entries = _hooks.setdefault(kind, {})
    entries[name] = callback

    def unregister() -> None:
        """旧注销句柄不能移除热重载后的新实现。"""
        if entries.get(name) is callback:
            entries.pop(name)

    return unregister


def _sync(callback: Callable[..., Any], *args: Any) -> Any:
    """验证同步返回，避免协程被当成有效过滤结果。"""
    value = callback(*args)
    if inspect.isawaitable(value):
        close = getattr(value, "close", None)
        if callable(close):
            close()
        raise TypeError("该 hook 必须同步返回")
    return value


async def _call(callback: Callable[..., Any], *args: Any) -> Any:
    """异步回调限时执行，同步回调必须由插件保证不阻塞。"""
    value = callback(*args)
    return await asyncio.wait_for(value, HOOK_TIMEOUT_SECONDS) if inspect.isawaitable(value) else value


async def guard(hook_kind: str, context: ChatHookContext, **details: Any) -> bool:
    """拒绝优先，None 表示不干预；异常、超时或非法返回均拒绝。"""
    if hook_kind not in {"request_guard", "tool_guard", "send_guard"}:
        raise ValueError("无效的守卫类型")
    for name, callback in tuple(_hooks.get(hook_kind, {}).items()):
        try:
            value = await _call(callback, context, _snapshot(details))
            if value is False:
                return False
            if value is not None and value is not True:
                raise TypeError("守卫只允许返回 bool 或 None")
        except Exception:
            logger.opt(exception=True).warning(f"[ChatHook] 守卫失败: {hook_kind}/{name}")
            return False
    return True


def resolve_persona(context: ChatHookContext, name: str, text: str) -> tuple[str, str]:
    """依次应用明确的人格覆盖值，错误时终止而非套用其他人格。"""
    for label, callback in tuple(_hooks.get("persona", {}).items()):
        try:
            value = _sync(callback, context, name, text)
            if value is None:
                continue
            if not isinstance(value, PersonaOverride):
                raise TypeError("人格 hook 必须返回 PersonaOverride 或 None")
            if value.name is not None:
                if not isinstance(value.name, str) or not value.name.strip():
                    raise ValueError("人格名称不能为空")
                name = value.name
                if value.text is None:
                    from .personas import get_persona_text

                    text = get_persona_text(name)
            if value.text is not None:
                if not isinstance(value.text, str):
                    raise TypeError("人格文本必须是字符串")
                text = value.text
        except Exception as exc:
            logger.opt(exception=True).warning(f"[ChatHook] 人格覆盖失败: {label}")
            raise ChatHookError("人格扩展失败") from exc
    return name, text


def system_prompt_sections(context: ChatHookContext) -> str:
    """独立解析并排序系统段，空贡献不改变原模板，失败时中止生成。"""
    sections: list[tuple[float, str, str]] = []
    for name, callback in tuple(_hooks.get("system_prompt", {}).items()):
        try:
            section = _sync(callback, context)
            if section is None:
                continue
            if not isinstance(section, SystemPromptSection) or not isinstance(section.text, str):
                raise TypeError("系统提示词 hook 必须返回 SystemPromptSection 或 None")
            if (
                isinstance(section.order, bool)
                or not isinstance(section.order, (int, float))
                or not isfinite(section.order)
            ):
                raise ValueError("系统提示词段 order 必须是有限数值")
            if section.text.strip():
                sections.append((section.order, name, section.text))
        except Exception as exc:
            logger.opt(exception=True).warning(f"[ChatHook] 系统提示词段失败: {name}")
            raise ChatHookError("系统提示词扩展失败") from exc
    return "\n\n".join(text for _, _, text in sorted(sections))


def prompt_notes(context: ChatHookContext) -> list[str]:
    """只追加独立参考，不允许改写历史、系统身份或工具协议。"""
    notes = []
    for name, callback in tuple(_hooks.get("prompt", {}).items()):
        try:
            note = _sync(callback, context)
            if note is None:
                continue
            if not isinstance(note, str):
                raise TypeError("提示 hook 只允许返回字符串或 None")
            if note.strip():
                notes.append(note)
        except Exception as exc:
            logger.opt(exception=True).warning(f"[ChatHook] 提示扩展失败: {name}")
            raise ChatHookError("提示扩展失败") from exc
    return notes


def filter_reply_text(context: ChatHookContext, text: str) -> str:
    """在断句和持久化前过滤生成正文，空串表示丢弃回复。"""
    for name, callback in tuple(_hooks.get("reply_text", {}).items()):
        try:
            result = _sync(callback, context, text)
            if result is not None:
                if not isinstance(result, str):
                    raise TypeError("正文 hook 只允许返回字符串或 None")
                text = result
        except Exception as exc:
            logger.opt(exception=True).warning(f"[ChatHook] 正文扩展失败: {name}")
            raise ChatHookError("正文扩展失败") from exc
    return text


async def notify(event: str, context: ChatHookContext, **details: Any) -> None:
    """生命周期通知不影响核心结果，每个订阅者收到独立快照。"""
    for name, callback in tuple(_hooks.get("observer", {}).items()):
        try:
            await _call(callback, event, context, _snapshot(details))
        except Exception:
            logger.opt(exception=True).warning(f"[ChatHook] 通知失败: {event}/{name}")
