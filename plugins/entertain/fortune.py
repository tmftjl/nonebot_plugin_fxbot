"""今日运势图片。"""

from __future__ import annotations

import base64
import io
import json
import math
import random
from datetime import datetime
from pathlib import Path
from typing import Any

from nonebot import get_driver
from nonebot.adapters import Bot, Event
from nonebot.matcher import Matcher
from PIL import Image, ImageDraw, ImageOps

from ...permission import PermLevel, PermScene
from ...plugin import Plugin
from ...adapter import build_message, build_message_segment
from ...utils.fonts import load_font
from ...utils.http import get_shared_async_client
from ...utils.paths import data_dir
from .config import cfg_api_urls

RESOURCE_DIR = Path(__file__).parent / "resource"
DATA_DIR = data_dir("entertain")
JRYS_DEFS_FILE = RESOURCE_DIR / "jrys_data.json"
USER_DATA_FILE = DATA_DIR / "user_fortunes.json"

_JRYS_DATA: list[dict[str, Any]] = []
_USER_FORTUNES: dict[str, dict[str, Any]] = {}

FONT_MAIN = load_font(RESOURCE_DIR / "font.ttf", 48)
FONT_LARGE = load_font(RESOURCE_DIR / "font.ttf", 90)
FONT_MEDIUM = load_font(RESOURCE_DIR / "font.ttf", 32)
FONT_SMALL = load_font(RESOURCE_DIR / "font.ttf", 26)
FONT_TINY = load_font(RESOURCE_DIR / "font.ttf", 22)

P = Plugin("entertain", display_name="娱乐", enabled=True, level=PermLevel.LOW, scene=PermScene.ALL)


def _load_fortune_defs() -> None:
    """加载运势定义。"""
    global _JRYS_DATA
    if not JRYS_DEFS_FILE.exists():
        _JRYS_DATA = []
        return
    try:
        data = json.loads(JRYS_DEFS_FILE.read_text(encoding="utf-8"))
        _JRYS_DATA = data if isinstance(data, list) else []
    except Exception:
        _JRYS_DATA = []


def _load_user_fortunes() -> None:
    """加载用户运势缓存。"""
    global _USER_FORTUNES
    if not USER_DATA_FILE.exists():
        _USER_FORTUNES = {}
        return
    try:
        data = json.loads(USER_DATA_FILE.read_text(encoding="utf-8"))
        _USER_FORTUNES = data if isinstance(data, dict) else {}
    except Exception:
        _USER_FORTUNES = {}


def _save_user_fortunes() -> None:
    """保存用户运势缓存。"""
    USER_DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    USER_DATA_FILE.write_text(json.dumps(_USER_FORTUNES, ensure_ascii=False, indent=2), encoding="utf-8")


@get_driver().on_startup
async def _on_startup() -> None:
    """启动时加载运势数据。"""
    _load_fortune_defs()
    _load_user_fortunes()


@get_driver().on_shutdown
async def _on_shutdown() -> None:
    """关闭时保存运势数据。"""
    _save_user_fortunes()


def _uid(event: Event) -> str:
    """提取用户 ID。"""
    if hasattr(event, "get_user_id"):
        try:
            return str(event.get_user_id())
        except Exception:
            pass
    return str(getattr(event, "user_id", "") or "")


def _nickname(event: Event, user_id: str) -> str:
    """提取用户昵称。"""
    sender = getattr(event, "sender", None)
    if sender:
        return str(getattr(sender, "card", None) or getattr(sender, "nickname", None) or f"用户{user_id}")
    return f"用户{user_id}"


def _num_to_chinese(num: int) -> str:
    """数字转中文日期。"""
    digits = "零一二三四五六七八九"
    if 1 <= num <= 9:
        return digits[num]
    if num == 10:
        return "十"
    if 10 < num < 20:
        return "十" + digits[num % 10]
    if num % 10 == 0:
        return digits[num // 10] + "十"
    return digits[num // 10] + "十" + digits[num % 10]


async def _get_background_image() -> Image.Image | None:
    """获取可选背景图。"""
    url = str(cfg_api_urls()["background_api"]).strip()
    if not url:
        return None
    try:
        client = await get_shared_async_client()
        response = await client.get(url)
        response.raise_for_status()
        return Image.open(io.BytesIO(response.content)).convert("RGBA")
    except Exception:
        return None


def _sanitize_stars(text: str) -> str:
    """保留星级字符。"""
    return "".join(ch for ch in (text or "") if ch in {"★", "☆"})


def _draw_wrapped_text(text: str, max_chars: int) -> str:
    """按字符宽度换行。"""
    lines: list[str] = []
    for paragraph in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        buffer = ""
        for ch in paragraph:
            if len(buffer) >= max_chars:
                lines.append(buffer)
                buffer = ch
            else:
                buffer += ch
        if buffer:
            lines.append(buffer)
    return "\n".join(lines)


def _draw_star_rating(
    draw: ImageDraw.ImageDraw,
    center_x: float,
    y: float,
    rating_string: str,
    star_size: int = 30,
    spacing: int = 10,
) -> None:
    """绘制旧版星级评分。"""
    stars = _sanitize_stars(rating_string)
    total_width = len(stars) * star_size + (len(stars) - 1) * spacing if stars else 0
    current_x = center_x - total_width / 2
    for ch in stars:
        cx = current_x + star_size / 2
        vertices = []
        for i in range(10):
            angle = math.pi / 5 * i - math.pi / 2
            radius = star_size / 2 if i % 2 == 0 else star_size / 4
            vertices.append((cx + radius * math.cos(angle), y + radius * math.sin(angle)))
        if ch == "★":
            draw.polygon(vertices, fill=(0, 0, 0, 220))
        else:
            draw.polygon(vertices, outline=(0, 0, 0, 220), width=2)
        current_x += star_size + spacing


def _generate_fortune_canvas(
    nickname: str,
    data: dict[str, Any],
    background: Image.Image | None = None,
) -> Image.Image:
    """生成旧版今日运势卡片。"""
    width, height = 650, 1000
    if background is None:
        image = Image.new("RGBA", (width, height), (255, 255, 255, 255))
    else:
        image = ImageOps.fit(background, (width, height), Image.Resampling.LANCZOS)
    overlay = Image.new("RGBA", (width, height), (255, 255, 255, 180))
    image = Image.alpha_composite(image.convert("RGBA"), overlay)
    draw = ImageDraw.Draw(image)

    fortune = data.get("fortune", {})
    y = 80
    cx = width / 2
    color = (0, 0, 0, 220)

    draw.text((cx, y), f"{nickname} 的{_num_to_chinese(datetime.now().day)}日运势", font=FONT_TINY, fill=color, anchor="mm")
    y += 80
    draw.text((cx, y), fortune.get("fortuneSummary", "今日运势"), font=FONT_LARGE, fill=color, anchor="mm")
    y += 120

    lucky_star = fortune.get("luckyStar", "")
    if lucky_star:
        _draw_star_rating(draw, cx, y, lucky_star)
    y += 100

    sign_text = fortune.get("signText", "")
    if sign_text:
        draw.text((cx, y), sign_text, font=FONT_MEDIUM, fill=color, anchor="mm")
        y += 80

    draw.line([(cx - 150, y), (cx + 150, y)], fill=(0, 0, 0, 100), width=2)
    y += 60

    wrapped = _draw_wrapped_text(fortune.get("unsignText", ""), 22)
    draw.multiline_text((cx, y), wrapped, font=FONT_SMALL, fill=color, anchor="ma", spacing=15, align="center")
    draw.text((cx, height - 50), "| 仅供参考，切勿拘泥 |", font=FONT_TINY, fill=(0, 0, 0, 150), anchor="mm")
    return image


def _pil_to_base64_image(image: Image.Image) -> str:
    """PIL 图片转 base64 图片段。"""
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return "base64://" + base64.b64encode(buffer.getvalue()).decode()


def _get_or_create_today_fortune(user_id: str) -> tuple[dict[str, Any], bool]:
    """获取或生成当天运势。"""
    if not _JRYS_DATA:
        _load_fortune_defs()
    today = datetime.now().strftime("%Y-%m-%d")
    record = _USER_FORTUNES.get(user_id)
    if record and record.get("time") == today:
        return record, False
    if not _JRYS_DATA:
        raise ValueError("运势库为空，无法生成")
    record = {"fortune": random.choice(_JRYS_DATA), "time": today}
    _USER_FORTUNES[user_id] = record
    return record, True


fortune_cmd = P.on_regex(
    r"^(#|/)(?:今日运势|运势|抽签)",
    name="today",
    display_name="今日运势",
    priority=5,
    block=True,
    level=PermLevel.LOW,
    scene=PermScene.ALL,
)


@fortune_cmd.handle()
async def _handle_fortune(matcher: Matcher, bot: Bot, event: Event) -> None:
    """发送今日运势图片。"""
    user_id = _uid(event)
    if not user_id:
        await matcher.finish("无法获取用户 ID")
    try:
        data, _ = _get_or_create_today_fortune(user_id)
    except Exception as exc:
        await matcher.finish(f"生成失败：{exc}")
    background = await _get_background_image()
    image = _generate_fortune_canvas(_nickname(event, user_id), data, background=background)
    await matcher.finish(build_message(bot, build_message_segment(bot, "image", image)))
