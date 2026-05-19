"""PermissionChecker 和 permission_for 辅助函数。"""

from __future__ import annotations

from typing import Any

from nonebot import get_driver
from nonebot.adapters import Bot, Event
from nonebot.permission import Permission

from .policy import PolicyChain
from .storage import get_storage
from .types import Decision, PermContext, PermLevel


def _normalize_id(value: Any) -> str | None:
    """标准化事件 ID。"""
    if value is None:
        return None
    text = str(value).strip()
    return text if text and text != "0" else None


def _uid(event: Any) -> str | None:
    """提取用户 ID。"""
    if hasattr(event, "get_user_id"):
        try:
            return _normalize_id(event.get_user_id())
        except Exception:
            pass
    return _normalize_id(getattr(event, "user_id", None))


def _gid(event: Any) -> str | None:
    """提取群 ID。"""
    if hasattr(event, "get_group_id"):
        try:
            return _normalize_id(event.get_group_id())
        except Exception:
            pass
    return _normalize_id(getattr(event, "group_id", None))


def _is_group_event(event: Any) -> bool:
    """判断是否为群事件。"""
    return _gid(event) is not None


def _is_private_event(event: Any) -> bool:
    """判断是否为私聊事件。"""
    return _uid(event) is not None and not _is_group_event(event)


def _is_superuser(user_id: str | None) -> bool:
    """判断用户是否为 NoneBot SUPERUSER。"""
    try:
        superusers = {str(item) for item in get_driver().config.superusers or []}
    except Exception:
        superusers = set()
    return bool(user_id and user_id in superusers)


def _has_group_role(event: Any, role: str) -> bool:
    """判断 OneBot 群身份。"""
    return _is_group_event(event) and str(getattr(getattr(event, "sender", None), "role", "")) == role


def _bot_admins(config: dict[str, Any]) -> set[str]:
    """从系统配置和权限配置中读取 bot_admins。"""
    ids: list[str] = []
    for source in (config, config.get("top") if isinstance(config.get("top"), dict) else {}):
        value = source.get("bot_admins") if isinstance(source, dict) else None
        if isinstance(value, (list, tuple, set)):
            ids.extend(str(item) for item in value if item is not None)
    return set(ids)


async def _user_level(event: Any, config: dict[str, Any]) -> PermLevel:
    """计算用户权限等级。"""
    user_id = _uid(event)
    if not user_id:
        return PermLevel.LOW
    if _is_superuser(user_id):
        return PermLevel.SUPERUSER
    if user_id in _bot_admins(config):
        return PermLevel.BOT_ADMIN
    if _is_group_event(event):
        if _has_group_role(event, "owner"):
            return PermLevel.OWNER
        if _has_group_role(event, "admin"):
            return PermLevel.ADMIN
        return PermLevel.MEMBER
    if _is_private_event(event):
        return PermLevel.MEMBER
    return PermLevel.LOW


class PermissionChecker:
    """权限检查器。"""

    def __init__(self) -> None:
        self._chain = PolicyChain()

    async def _build_context(self, event: Any, config: dict[str, Any]) -> PermContext:
        """构建权限上下文。"""
        return PermContext(
            user_id=_uid(event) or "",
            group_id=_gid(event),
            user_level=await _user_level(event, config),
            is_group=_is_group_event(event),
            is_private=_is_private_event(event),
        )

    def _parse_feature(self, feature: str) -> tuple[str | None, str | None]:
        """解析 feature，格式为 plugin 或 plugin:command。"""
        parts = [part.strip() for part in str(feature or "").split(":") if part.strip()]
        if len(parts) >= 2:
            return parts[0], parts[1]
        if len(parts) == 1:
            return parts[0], None
        return None, None

    async def check(self, feature: str, bot: Bot, event: Event) -> bool:
        """检查指定 feature 是否允许当前事件调用。"""
        config = get_storage().load()
        if not config:
            return True

        plugin, command = self._parse_feature(feature)
        context = await self._build_context(event, config)
        sub_plugins = config.get("sub_plugins") if isinstance(config.get("sub_plugins"), dict) else {}
        plugin_node = sub_plugins.get(plugin, {}) if plugin else {}
        commands = plugin_node.get("commands") if isinstance(plugin_node.get("commands"), dict) else {}

        layers = [
            config.get("top"),
            plugin_node.get("top") if plugin else None,
            commands.get(command) if command else None,
        ]

        for layer in layers:
            if not isinstance(layer, dict):
                continue
            result = await self._chain.evaluate(layer, context)
            if result.decision == Decision.DENY:
                return False
        return True


_global_checker = PermissionChecker()


def permission_for(feature: str, *, category: str = "sub") -> Permission:
    """构造 NoneBot Permission。"""

    async def _checker(bot: Bot, event: Event) -> bool:
        if category != "sub":
            return True
        return await _global_checker.check(feature, bot, event)

    return Permission(_checker)


def permission_for_plugin(plugin: str, *, category: str = "sub") -> Permission:
    """构造插件级权限。"""
    return permission_for(plugin, category=category)


def permission_for_cmd(plugin: str, command: str, *, category: str = "sub") -> Permission:
    """构造命令级权限。"""
    return permission_for(f"{plugin}:{command}", category=category)
