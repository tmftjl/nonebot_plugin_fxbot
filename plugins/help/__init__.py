"""内置帮助插件。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nonebot.adapters import Bot
from nonebot.matcher import Matcher
from nonebot.params import RegexGroup
from PIL import Image, ImageDraw

from ...adapter import build_message, build_message_segment
from ...permission import PermLevel, PermScene
from ...plugin import Plugin
from ...utils.fonts import get_shared_font_path, load_font
from .config import HelpConfigRef, load_help_config, resolve_help_config

try:
    from .renderer import render_help_image
except Exception:  # pragma: no cover
    render_help_image = None

P = Plugin("help", display_name="帮助", enabled=True, level=PermLevel.LOW, scene=PermScene.ALL)

help_cmd = P.on_regex(
    r"^(?:#|＃|/)(.*?)帮助$",
    name="help",
    display_name="帮助",
    priority=5,
    block=True,
    level=PermLevel.LOW,
    scene=PermScene.ALL,
)


def _resolve_config_ref(keyword: str | None) -> HelpConfigRef | None:
    """解析帮助图配置。"""
    return resolve_help_config(keyword)


def _asset_path(config: dict[str, Any], field: str) -> Path | None:
    """解析帮助配置中的资源路径。"""
    value = str(config.get(field) or "").strip()
    if not value:
        return None
    path = Path(value)
    if not path.is_absolute():
        path = Path(str(config.get("_base_dir") or "")) / path
    return path if path.is_file() else None


def _fallback_image(title: str, sub_title: str, groups_data: list[dict[str, Any]]) -> bytes:
    """帮助图兜底渲染。"""
    text = f"{title}\n{sub_title}\n\n" + "\n".join(
        f"【{group.get('group', '')}】 "
        + ", ".join(str(item.get("title", "")) for item in (group.get("list") or []))
        for group in groups_data
    )
    lines = text.split("\n")
    font = load_font(get_shared_font_path(), 24)
    width = max(480, max((len(line) for line in lines), default=20) * 14 + 40)
    height = max(320, 30 + len(lines) * 32 + 30)
    image = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    y = 20
    for line in lines:
        draw.text((20, y), line, font=font, fill=(32, 32, 32))
        y += 32
    import io

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


@help_cmd.handle()
async def _handle_help(matcher: Matcher, bot: Bot, groups: tuple = RegexGroup()) -> None:
    """发送旧版排版帮助图。"""
    keyword = str(groups[0]).strip() if groups and groups[0] else None
    resolved_ref = _resolve_config_ref(keyword)
    if keyword and resolved_ref is None:
        await matcher.skip()

    config = load_help_config(resolved_ref)
    title = str(config.get("title") or "帮助")
    sub_title = str(config.get("sub_title") or (keyword or ""))
    footer = config.get("footer")
    col_count = int(config.get("col_count", 3) or 3)
    groups_data = config.get("groups", []) or []
    background = _asset_path(config, "background")
    icon = _asset_path(config, "icon")

    tmp_dir = Path(__file__).parent / "temp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    config_key = str(config.get("_config_key") or "help")
    cache_file = tmp_dir / f"{config_key}.png"
    cfg_path = Path(str(config.get("_config_path") or ""))

    try:
        code_files = [
            Path(__file__),
            Path(__file__).parent / "renderer.py",
            Path(__file__).parent / "config.py",
        ]
        asset_files = [path for path in [background, icon] if path is not None]
        mtimes = [
            path.stat().st_mtime for path in [*code_files, cfg_path, *asset_files] if path.exists()
        ]
        cache_valid = cache_file.exists() and cache_file.stat().st_mtime >= max(mtimes, default=0)
    except Exception:
        cache_valid = False

    image_bytes = cache_file.read_bytes() if cache_valid else b""
    if not image_bytes and render_help_image is not None:
        image_bytes = await render_help_image(
            title=title,
            sub_title=sub_title,
            groups=groups_data,
            col_count=col_count,
            footer=str(footer) if footer is not None else None,
            background=background,
            icon=icon,
        )
        if image_bytes:
            cache_file.write_bytes(image_bytes)
    if not image_bytes:
        image_bytes = _fallback_image(title, sub_title, groups_data)

    await matcher.finish(build_message(bot, build_message_segment(bot, "image", image_bytes)))
