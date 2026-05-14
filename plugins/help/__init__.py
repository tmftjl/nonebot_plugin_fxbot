"""内置帮助插件。"""

from __future__ import annotations

from nonebot.matcher import Matcher

from ...permission import PermLevel, PermScene
from ...plugin import Plugin, get_command_display_names, get_plugin_display_names

P = Plugin("help", display_name="帮助", enabled=True, level=PermLevel.LOW, scene=PermScene.ALL)

help_cmd = P.on_regex(
    r"^(?:#|/)?(?:帮助|菜单|功能)$",
    name="help",
    display_name="帮助",
    priority=5,
    block=True,
    level=PermLevel.LOW,
    scene=PermScene.ALL,
)


def _build_help_text() -> str:
    """根据已注册展示名生成文本帮助。"""
    plugin_names = get_plugin_display_names()
    command_names = get_command_display_names()
    lines = ["FxBot 帮助"]
    for plugin, display in sorted(plugin_names.items()):
        commands = command_names.get(plugin, {})
        if not commands:
            lines.append(f"- {display}")
            continue
        command_text = "、".join(
            f"{command_display}({command_name})"
            for command_name, command_display in sorted(commands.items())
        )
        lines.append(f"- {display}: {command_text}")
    if len(lines) == 1:
        lines.append("暂无已注册命令")
    return "\n".join(lines)


@help_cmd.handle()
async def _handle_help(matcher: Matcher) -> None:
    """发送帮助文本。"""
    await matcher.finish(_build_help_text())
