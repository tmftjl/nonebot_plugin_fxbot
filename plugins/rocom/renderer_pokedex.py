"""洛克王国图鉴图片渲染。"""

from __future__ import annotations

import textwrap
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from ...utils.fonts import load_font
from .data import SKILL_LIST_FIELDS

RESOURCE_DIR = Path(__file__).parent / "resources"
POKEDEX_DIR = RESOURCE_DIR / "pokedex"
FONT_DIR = RESOURCE_DIR / "fonts"

FONT_TITLE = load_font(FONT_DIR / "rocom_origin.ttf", 68)
FONT_SUBTITLE = load_font(FONT_DIR / "rocom_origin.ttf", 42)
FONT_SECTION = load_font(FONT_DIR / "rocom_origin.ttf", 32)
FONT_TEXT = load_font(FONT_DIR / "skill_origin.ttf", 26)
FONT_SMALL = load_font(FONT_DIR / "skill_origin.ttf", 22)
FONT_TINY = load_font(FONT_DIR / "skill_origin.ttf", 18)

TEXT_COLOR = (100, 92, 79)
SUB_TEXT_COLOR = (116, 126, 142)
WHITE = (255, 255, 255)
PANEL = (255, 250, 236)
PANEL_LINE = (225, 204, 166)

ATTR_LABELS = [
    ("HP", "attr_hp"),
    ("物攻", "attr_atk"),
    ("魔攻", "attr_spatk"),
    ("物防", "attr_def"),
    ("魔防", "attr_spdef"),
    ("速度", "attr_spd"),
]

TYPE_COLORS = {
    "冰": (95, 173, 221),
    "草": (78, 188, 115),
    "虫": (158, 206, 33),
    "地": (154, 126, 63),
    "电": (231, 197, 6),
    "毒": (186, 98, 224),
    "恶": (207, 70, 122),
    "光": (79, 192, 255),
    "幻": (159, 167, 248),
    "火": (219, 85, 37),
    "机械": (64, 203, 169),
    "龙": (237, 73, 98),
    "萌": (252, 124, 172),
    "普通": (63, 137, 180),
    "水": (106, 169, 254),
    "无": (186, 187, 198),
    "武": (255, 150, 54),
    "翼": (62, 199, 202),
    "幽": (148, 70, 236),
}


def _round_rect(draw: ImageDraw.ImageDraw, xy: tuple[int, int, int, int], fill: tuple[int, int, int], outline: tuple[int, int, int] | None = None) -> None:
    draw.rounded_rectangle(xy, radius=8, fill=fill, outline=outline, width=2 if outline else 1)


def _wrap(text: str, width: int) -> list[str]:
    if not text:
        return []
    lines: list[str] = []
    for paragraph in text.splitlines():
        lines.extend(textwrap.wrap(paragraph, width=width, break_long_words=False, replace_whitespace=False) or [""])
    return lines


def _display_name(pet: dict[str, Any]) -> str:
    name = str(pet.get("name") or "")
    form = str(pet.get("form") or "")
    return f"{name}{form}" if form else name


def _section(draw: ImageDraw.ImageDraw, title: str, x: int, y: int, width: int) -> None:
    color = (88, 148, 202)
    draw.rounded_rectangle((x, y, x + width, y + 44), radius=8, fill=color)
    draw.text((x + 24, y + 22), title, WHITE, FONT_SECTION, "lm")


def _draw_type_badge(img: Image.Image, draw: ImageDraw.ImageDraw, name: str, x: int, y: int) -> int:
    icon_path = POKEDEX_DIR / f"{name}.png"
    if icon_path.is_file():
        icon = Image.open(icon_path).convert("RGBA").resize((62, 62))
        img.paste(icon, (x, y), icon)
        return 70
    color = TYPE_COLORS.get(name, (140, 151, 166))
    draw.rounded_rectangle((x, y + 10, x + 92, y + 52), radius=8, fill=color)
    draw.text((x + 46, y + 31), name, WHITE, FONT_SMALL, "mm")
    return 100


def _draw_stats(draw: ImageDraw.ImageDraw, pet: dict[str, Any], x: int, y: int) -> None:
    attr = pet.get("attribute") or {}
    max_value = max([int(attr.get(field) or 0) for _, field in ATTR_LABELS] + [1])
    for index, (label, field) in enumerate(ATTR_LABELS):
        row_y = y + index * 48
        value = int(attr.get(field) or 0)
        draw.text((x, row_y + 22), label, TEXT_COLOR, FONT_SMALL, "lm")
        draw.rounded_rectangle((x + 92, row_y + 9, x + 350, row_y + 35), radius=6, fill=(235, 225, 205))
        bar_width = int(258 * value / max_value)
        draw.rounded_rectangle((x + 92, row_y + 9, x + 92 + bar_width, row_y + 35), radius=6, fill=(91, 159, 214))
        draw.text((x + 385, row_y + 22), str(value), TEXT_COLOR, FONT_SMALL, "rm")


def _collect_skills(pet: dict[str, Any]) -> list[dict[str, Any]]:
    skills: list[dict[str, Any]] = []
    seen: set[str] = set()
    for field in SKILL_LIST_FIELDS:
        for skill in pet.get(field) or []:
            if not isinstance(skill, dict):
                continue
            name = str(skill.get("name") or "")
            if not name or name in seen:
                continue
            seen.add(name)
            skills.append(skill)
    return skills


def _draw_skills(draw: ImageDraw.ImageDraw, pet: dict[str, Any], x: int, y: int) -> int:
    skills = _collect_skills(pet)
    if not skills:
        draw.text((x, y), "暂无技能数据", SUB_TEXT_COLOR, FONT_TEXT, "la")
        return y + 40
    shown = skills[:18]
    col_width = 360
    row_height = 58
    for index, skill in enumerate(shown):
        col = index % 3
        row = index // 3
        sx = x + col * col_width
        sy = y + row * row_height
        family = str(skill.get("families") or "无")
        color = TYPE_COLORS.get(family, (124, 143, 161))
        draw.rounded_rectangle((sx, sy, sx + 330, sy + 48), radius=8, fill=(248, 242, 226), outline=PANEL_LINE, width=1)
        draw.rounded_rectangle((sx + 10, sy + 9, sx + 64, sy + 39), radius=6, fill=color)
        draw.text((sx + 37, sy + 24), family[:2], WHITE, FONT_TINY, "mm")
        name = str(skill.get("name") or "")[:9]
        draw.text((sx + 76, sy + 17), name, TEXT_COLOR, FONT_SMALL, "la")
        power = str(skill.get("power") or "0")
        cost = str(skill.get("cost") or "0")
        draw.text((sx + 76, sy + 39), f"威力 {power} / 消耗 {cost}", SUB_TEXT_COLOR, FONT_TINY, "la")
    bottom = y + ((len(shown) + 2) // 3) * row_height
    if len(skills) > len(shown):
        draw.text((x, bottom + 6), f"另有 {len(skills) - len(shown)} 个技能未展示，可用 #技能信息 查询详情", SUB_TEXT_COLOR, FONT_SMALL, "la")
        bottom += 38
    return bottom


async def render_pokedex_image(pet: dict[str, Any], pet_id: str) -> bytes:
    """生成精灵图鉴图片。"""
    skills = _collect_skills(pet)
    skill_rows = max(1, min(6, (min(len(skills), 18) + 2) // 3))
    desc_lines = _wrap(str(pet.get("description") or ""), 44)
    feature = pet.get("feature") or {}
    feature_lines = _wrap(str(feature.get("desc") or ""), 42)
    height = max(1240, 1020 + skill_rows * 58 + len(desc_lines) * 34 + len(feature_lines) * 34)

    bg_path = POKEDEX_DIR / "bg.jpg"
    if bg_path.is_file():
        img = Image.open(bg_path).convert("RGB").resize((1200, height))
    else:
        img = Image.new("RGB", (1200, height), (235, 229, 207))
    draw = ImageDraw.Draw(img)

    title_path = POKEDEX_DIR / "title.png"
    if title_path.is_file():
        title = Image.open(title_path).convert("RGBA")
        img.paste(title, (0, 0), title)
    else:
        draw.rectangle((0, 0, 1200, 180), fill=(92, 154, 211))
    draw.text((600, 96), "精灵图鉴", WHITE, FONT_TITLE, "mm")

    name = _display_name(pet)
    draw.text((600, 244), name, TEXT_COLOR, FONT_TITLE, "mm")
    draw.text((600, 300), f"编号 {pet_id}", SUB_TEXT_COLOR, FONT_TEXT, "mm")

    types = [str(item) for item in pet.get("unit_type") or [] if item]
    tx = 498
    for type_name in types:
        tx += _draw_type_badge(img, draw, type_name, tx, 328)

    x, y = 70, 405
    _round_rect(draw, (x, y, x + 470, y + 420), PANEL, PANEL_LINE)
    primary_type = types[0] if types else "普通"
    draw.rounded_rectangle((x + 45, y + 40, x + 425, y + 360), radius=16, fill=TYPE_COLORS.get(primary_type, (166, 179, 191)))
    icon_path = POKEDEX_DIR / "icon.png"
    if icon_path.is_file():
        icon = Image.open(icon_path).convert("RGBA").resize((150, 150))
        img.paste(icon, (x + 185, y + 122), icon)
    draw.text((x + 235, y + 335), "暂无本地立绘", WHITE, FONT_SECTION, "mm")

    _round_rect(draw, (590, y, 1130, y + 420), PANEL, PANEL_LINE)
    _section(draw, "种族值", 620, y + 28, 470)
    _draw_stats(draw, pet, 650, y + 100)

    info_y = y + 460
    _round_rect(draw, (70, info_y, 1130, info_y + 210), PANEL, PANEL_LINE)
    _section(draw, "基础信息", 100, info_y + 24, 1000)
    groups = "、".join(str(item) for item in pet.get("egg_group") or []) or "无"
    size = f"{(int(pet.get('height_low') or 0) / 100):.2f}~{(int(pet.get('height_high') or 0) / 100):.2f}m"
    weight = f"{(int(pet.get('weight_low') or 0) / 1000):.2f}~{(int(pet.get('weight_high') or 0) / 1000):.2f}kg"
    draw.text((120, info_y + 92), f"蛋组：{groups}", TEXT_COLOR, FONT_TEXT, "la")
    draw.text((120, info_y + 132), f"身高：{size}", TEXT_COLOR, FONT_TEXT, "la")
    draw.text((520, info_y + 132), f"体重：{weight}", TEXT_COLOR, FONT_TEXT, "la")
    feature_name = str(feature.get("name") or "无")
    draw.text((120, info_y + 172), f"特性：{feature_name}", TEXT_COLOR, FONT_TEXT, "la")

    desc_y = info_y + 250
    desc_height = 128 + max(len(desc_lines), 1) * 34
    _round_rect(draw, (70, desc_y, 1130, desc_y + desc_height), PANEL, PANEL_LINE)
    _section(draw, "精灵描述", 100, desc_y + 24, 1000)
    for index, line in enumerate(desc_lines or ["暂无描述"]):
        draw.text((120, desc_y + 94 + index * 34), line, TEXT_COLOR, FONT_TEXT, "la")

    feature_y = desc_y + desc_height + 40
    feature_height = 128 + max(len(feature_lines), 1) * 34
    _round_rect(draw, (70, feature_y, 1130, feature_y + feature_height), PANEL, PANEL_LINE)
    _section(draw, "特性说明", 100, feature_y + 24, 1000)
    for index, line in enumerate(feature_lines or ["暂无特性说明"]):
        draw.text((120, feature_y + 94 + index * 34), line, TEXT_COLOR, FONT_TEXT, "la")

    skill_y = feature_y + feature_height + 40
    skill_height = max(150, skill_rows * 58 + 120)
    _round_rect(draw, (70, skill_y, 1130, skill_y + skill_height), PANEL, PANEL_LINE)
    _section(draw, "技能预览", 100, skill_y + 24, 1000)
    _draw_skills(draw, pet, 105, skill_y + 94)

    output = BytesIO()
    img.save(output, format="PNG")
    return output.getvalue()
