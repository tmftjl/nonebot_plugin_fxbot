"""自动注入权限的 Plugin 包装器。"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from nonebot import logger
from nonebot.adapters import Event
from nonebot.matcher import Matcher
from nonebot.message import event_preprocessor
from nonebot.permission import SUPERUSER, Permission

from ..adapter.message import move_non_text_segments_to_end
from ..permission import PermLevel, PermScene, permission_for_cmd, permission_for_plugin
from ..permission.helpers import upsert_command_defaults, upsert_plugin_defaults

_PLUGIN_DISPLAY_NAMES: dict[str, str] = {}
_COMMAND_DISPLAY_NAMES: dict[str, dict[str, str]] = {}


@event_preprocessor
async def _normalize_message_segments(event: Event) -> None:
    """统一将文本段前置，避免 @、图片等非文本段打断命令匹配。"""
    move_non_text_segments_to_end(event)


def _level_to_str(value: PermLevel | str | None) -> str | None:
    """将权限等级转换为配置字符串。"""
    if value is None:
        return None
    if isinstance(value, PermLevel):
        return {
            PermLevel.LOW: "all",
            PermLevel.MEMBER: "member",
            PermLevel.ADMIN: "admin",
            PermLevel.OWNER: "owner",
            PermLevel.BOT_ADMIN: "bot_admin",
            PermLevel.SUPERUSER: "superuser",
        }[value]
    return str(value).lower()


def _scene_to_str(value: PermScene | str | None) -> str | None:
    """将权限场景转换为配置字符串。"""
    if value is None:
        return None
    if isinstance(value, PermScene):
        return value.value
    return str(value).lower()


def _validate_entry(
    *,
    enabled: bool | None = None,
    level: PermLevel | None = None,
    scene: PermScene | None = None,
    wl_users: list[str] | None = None,
    wl_groups: list[str] | None = None,
    bl_users: list[str] | None = None,
    bl_groups: list[str] | None = None,
) -> None:
    """校验权限默认配置参数。"""
    if enabled is not None and not isinstance(enabled, bool):
        raise TypeError("enabled 必须是 bool")
    if level is not None and not isinstance(level, PermLevel):
        raise TypeError("level 必须是 PermLevel")
    if scene is not None and not isinstance(scene, PermScene):
        raise TypeError("scene 必须是 PermScene")

    for name, value in {
        "wl_users": wl_users,
        "wl_groups": wl_groups,
        "bl_users": bl_users,
        "bl_groups": bl_groups,
    }.items():
        if value is not None and not isinstance(value, (list, tuple, set)):
            raise TypeError(f"{name} 必须是 ID 列表")


def set_plugin_display_name(plugin: str, display_name: str) -> None:
    """设置插件展示名。"""
    plugin_name = str(plugin).strip()
    display = str(display_name).strip()
    if plugin_name and display:
        _PLUGIN_DISPLAY_NAMES[plugin_name] = display


def get_plugin_display_names() -> dict[str, str]:
    """获取插件展示名映射。"""
    return dict(_PLUGIN_DISPLAY_NAMES)


def set_command_display_name(plugin: str, command: str, display_name: str) -> None:
    """设置命令展示名。"""
    plugin_name = str(plugin).strip()
    command_name = str(command).strip()
    display = str(display_name).strip()
    if plugin_name and command_name and display:
        _COMMAND_DISPLAY_NAMES.setdefault(plugin_name, {})[command_name] = display


def get_command_display_names() -> dict[str, dict[str, str]]:
    """获取命令展示名映射。"""
    return {plugin: dict(commands) for plugin, commands in _COMMAND_DISPLAY_NAMES.items()}


class Plugin:
    """子插件注册包装器。"""

    def __init__(
        self,
        name: str,
        *,
        category: str = "sub",
        display_name: str | None = None,
        enabled: bool | None = None,
        level: PermLevel | None = None,
        scene: PermScene | None = None,
        wl_users: list[str] | None = None,
        wl_groups: list[str] | None = None,
        bl_users: list[str] | None = None,
        bl_groups: list[str] | None = None,
    ) -> None:
        self.name = name
        self.category = category if category in {"sub", "system"} else "sub"
        self._cmd_levels: dict[str, PermLevel | None] = {}

        if display_name:
            set_plugin_display_name(self.name, display_name)

        _validate_entry(
            enabled=enabled,
            level=level,
            scene=scene,
            wl_users=wl_users,
            wl_groups=wl_groups,
            bl_users=bl_users,
            bl_groups=bl_groups,
        )

        if self.category == "sub":
            upsert_plugin_defaults(
                self.name,
                enabled=enabled,
                level=_level_to_str(level),
                scene=_scene_to_str(scene),
                wl_users=wl_users,
                wl_groups=wl_groups,
                bl_users=bl_users,
                bl_groups=bl_groups,
            )

    def permission(self) -> Permission:
        """返回插件级权限。"""
        if self.category == "system":
            return Permission()
        return permission_for_plugin(self.name, category=self.category)

    def permission_cmd(self, command: str) -> Permission:
        """返回命令级权限。"""
        if self.category == "system":
            if self._cmd_levels.get(command) == PermLevel.SUPERUSER:
                return SUPERUSER
            return Permission()
        return permission_for_cmd(self.name, command, category=self.category)

    def _create_matcher(
        self,
        factory: Callable[..., type[Matcher]],
        *factory_args: Any,
        name: str,
        display_name: str | None = None,
        enabled: bool | None = None,
        level: PermLevel | None = None,
        scene: PermScene | None = None,
        wl_users: list[str] | None = None,
        wl_groups: list[str] | None = None,
        bl_users: list[str] | None = None,
        bl_groups: list[str] | None = None,
        log: bool = True,
        **kwargs: Any,
    ) -> type[Matcher]:
        """创建 matcher 并注入权限。"""
        if display_name:
            set_command_display_name(self.name, name, display_name)

        _validate_entry(
            enabled=enabled,
            level=level,
            scene=scene,
            wl_users=wl_users,
            wl_groups=wl_groups,
            bl_users=bl_users,
            bl_groups=bl_groups,
        )

        if self.category == "sub":
            upsert_command_defaults(
                self.name,
                name,
                enabled=enabled,
                level=_level_to_str(level),
                scene=_scene_to_str(scene),
                wl_users=wl_users,
                wl_groups=wl_groups,
                bl_users=bl_users,
                bl_groups=bl_groups,
            )
            kwargs.setdefault("permission", self.permission_cmd(name))
        else:
            self._cmd_levels[name] = level
            if level == PermLevel.SUPERUSER:
                kwargs.setdefault("permission", SUPERUSER)

        matcher = factory(*factory_args, **kwargs)

        if not (isinstance(matcher, type) and issubclass(matcher, Matcher)):
            raise TypeError(f"NoneBot matcher 工厂返回了无效类型: {type(matcher).__name__}")

        if log:
            plugin_display = _PLUGIN_DISPLAY_NAMES.get(self.name, self.name)
            command_display = display_name or name

            async def _log_command_entry() -> None:
                logger.opt(colors=True).info(
                    f"[<y>{plugin_display}</y>·<g>{command_display}</g>] 命令触发"
                )

            matcher.append_handler(_log_command_entry)

        return matcher

    if TYPE_CHECKING:

        def on_command(
            self,
            cmd: str | tuple[str, ...],
            *,
            name: str,
            display_name: str | None = None,
            enabled: bool | None = None,
            level: PermLevel | None = None,
            scene: PermScene | None = None,
            wl_users: list[str] | None = None,
            wl_groups: list[str] | None = None,
            bl_users: list[str] | None = None,
            bl_groups: list[str] | None = None,
            log: bool = False,
            **kwargs: Any,
        ) -> type[Matcher]: ...

        def on_regex(
            self,
            pattern: str,
            *,
            name: str,
            display_name: str | None = None,
            enabled: bool | None = None,
            level: PermLevel | None = None,
            scene: PermScene | None = None,
            wl_users: list[str] | None = None,
            wl_groups: list[str] | None = None,
            bl_users: list[str] | None = None,
            bl_groups: list[str] | None = None,
            log: bool = True,
            **kwargs: Any,
        ) -> type[Matcher]: ...

        def on_message(
            self,
            *,
            name: str,
            display_name: str | None = None,
            enabled: bool | None = None,
            level: PermLevel | None = None,
            scene: PermScene | None = None,
            wl_users: list[str] | None = None,
            wl_groups: list[str] | None = None,
            bl_users: list[str] | None = None,
            bl_groups: list[str] | None = None,
            log: bool = True,
            **kwargs: Any,
        ) -> type[Matcher]: ...

    def __getattr__(self, attr_name: str) -> Callable[..., type[Matcher]]:
        """动态代理 NoneBot 的 on_* matcher 工厂。"""
        if not (attr_name == "on" or attr_name.startswith("on_")):
            raise AttributeError(f"{type(self).__name__} 没有属性 {attr_name}")

        import nonebot

        try:
            factory = getattr(nonebot, attr_name)
        except AttributeError as exc:
            raise AttributeError(f"NoneBot 不支持 matcher 工厂 {attr_name}") from exc

        if not callable(factory):
            raise AttributeError(f"{attr_name} 不是可调用对象")

        def _wrapper(
            *factory_args: Any,
            name: str,
            display_name: str | None = None,
            enabled: bool | None = None,
            level: PermLevel | None = None,
            scene: PermScene | None = None,
            wl_users: list[str] | None = None,
            wl_groups: list[str] | None = None,
            bl_users: list[str] | None = None,
            bl_groups: list[str] | None = None,
            log: bool = True,
            **kwargs: Any,
        ) -> type[Matcher]:
            return self._create_matcher(
                factory,
                *factory_args,
                name=name,
                display_name=display_name,
                enabled=enabled,
                level=level,
                scene=scene,
                wl_users=wl_users,
                wl_groups=wl_groups,
                bl_users=bl_users,
                bl_groups=bl_groups,
                log=log,
                **kwargs,
            )

        return _wrapper
