"""插件可注册会话解析和隔离策略；核心不识别任何业务命名空间。"""

from __future__ import annotations

from typing import Any
from inspect import isawaitable, iscoroutinefunction
from dataclasses import dataclass
from collections.abc import Callable

from nonebot import logger

SessionResolver = Callable[[Any, Any, str], str | None]


@dataclass(frozen=True)
class SessionPolicy:
    """会话能力与响应节奏，多个插件取交集，不改变权限和长期记忆读写。"""

    allow_focus: bool = True
    allow_sharing: bool = True
    allow_debounce: bool = True
    allow_typing: bool = True
    allow_split: bool = True


PolicyResolver = Callable[[str], SessionPolicy | None]
_resolvers: dict[str, SessionResolver] = {}
_policies: dict[str, PolicyResolver] = {}
_EVENT_ATTR = "_fxbot_resolved_sessions"


def register_session_resolver(name: str, resolver: SessionResolver) -> Callable[[], None]:
    """按注册顺序添加同步解析器，同名替换不改优先级，返回精确注销句柄。"""
    if not isinstance(name, str) or not name.strip() or not callable(resolver) or iscoroutinefunction(resolver):
        raise ValueError("会话解析器需要名称和可调用对象")
    _resolvers[name] = resolver

    def unregister() -> None:
        """只移除本次注册，避免旧插件清理移除热重载后的新实现。"""
        if _resolvers.get(name) is resolver:
            _resolvers.pop(name)

    return unregister


def register_session_policy(name: str, resolver: PolicyResolver) -> Callable[[], None]:
    """注册同步策略解析器，策略每次使用重新读取以支持撤权和配置热更新。"""
    if not isinstance(name, str) or not name.strip() or not callable(resolver) or iscoroutinefunction(resolver):
        raise ValueError("会话策略需要名称和可调用对象")
    _policies[name] = resolver

    def unregister() -> None:
        """只注销当前实现。"""
        if _policies.get(name) is resolver:
            _policies.pop(name)

    return unregister


def session_policy(session_id: str) -> SessionPolicy:
    """合并所有限制，策略异常时关闭受控能力，禁止降级到无限制。"""
    result = SessionPolicy()
    for name, resolver in tuple(_policies.items()):
        try:
            policy = resolver(session_id)
            if isawaitable(policy):
                close = getattr(policy, "close", None)
                if callable(close):
                    close()
                raise TypeError("会话策略必须同步返回")
            if policy is None:
                continue
            if not isinstance(policy, SessionPolicy) or any(
                type(value) is not bool
                for value in (
                    policy.allow_focus,
                    policy.allow_sharing,
                    policy.allow_debounce,
                    policy.allow_typing,
                    policy.allow_split,
                )
            ):
                raise TypeError("会话策略必须返回 SessionPolicy 或 None")
            result = SessionPolicy(
                result.allow_focus and policy.allow_focus,
                result.allow_sharing and policy.allow_sharing,
                result.allow_debounce and policy.allow_debounce,
                result.allow_typing and policy.allow_typing,
                result.allow_split and policy.allow_split,
            )
        except Exception:
            logger.opt(exception=True).error(f"[ChatSession] 会话策略失败: {name}")
            return SessionPolicy(False, False)
    return result


def can_share_sessions(source: str, target: str) -> bool:
    """跨会话共享要求两端均允许，自身会话不受共享开关影响。"""
    return source == target or (session_policy(source).allow_sharing and session_policy(target).allow_sharing)


def resolve_session_id(bot: Any, event: Any, base_session_id: str) -> str:
    """首个非空覆盖生效，结果固定在事件对象上；解析失败拒绝处理而非退回普通会话。"""
    cached = getattr(event, _EVENT_ATTR, None)
    if isinstance(cached, dict) and base_session_id in cached:
        resolved = cached[base_session_id]
        if resolved is None:
            raise RuntimeError("会话解析已失败")
        return resolved
    if not _resolvers and not hasattr(event, "__dict__"):
        return base_session_id
    resolved = base_session_id
    try:
        values = dict(cached) if isinstance(cached, dict) else {}
        # 先占位阻止解析器递归调用 session_id_for_event；失败占位也不会退回普通会话。
        values[base_session_id] = None
        object.__setattr__(event, _EVENT_ATTR, values)
        for resolver in tuple(_resolvers.values()):
            candidate = resolver(bot, event, base_session_id)
            if isawaitable(candidate):
                close = getattr(candidate, "close", None)
                if callable(close):
                    close()
                raise TypeError("会话解析器必须同步返回")
            if candidate is None or candidate == "":
                continue
            if not isinstance(candidate, str) or not candidate.strip():
                raise TypeError("会话解析器必须返回非空字符串或 None")
            # 只允许修改命名空间，不能借会话覆盖伪造收件人或群聊/私聊类型。
            parts = candidate.rsplit(":", 2)
            if (
                len(parts) != 3
                or not parts[0]
                or ":" not in parts[0]
                or candidate != candidate.strip()
                # 平台名可包含空格（如 OneBot V11），其余部分仍禁止空白。
                or any(char.isspace() for char in candidate.partition(":")[2])
                or parts[-2:] != base_session_id.rsplit(":", 2)[-2:]
                or parts[0].split(":", 1)[0] != base_session_id.split(":", 1)[0]
            ):
                raise ValueError("会话解析器不能修改会话类型和目标 ID")
            resolved = candidate
            break
        values = dict(cached) if isinstance(cached, dict) else {}
        values[base_session_id] = resolved
        object.__setattr__(event, _EVENT_ATTR, values)
    except Exception:
        # 同一事件失败后不得因开关变化再次解析进另一个会话。
        try:
            values = dict(cached) if isinstance(cached, dict) else {}
            values[base_session_id] = None
            object.__setattr__(event, _EVENT_ATTR, values)
        except (AttributeError, TypeError):
            pass
        logger.opt(exception=True).error("[ChatSession] 会话解析失败，拒绝回退基础会话")
        raise
    return resolved
