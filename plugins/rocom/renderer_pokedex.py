"""洛克王国图鉴图片渲染。"""

from __future__ import annotations

import math
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from ...utils.fonts import load_font
from ...utils.paths import data_dir

RESOURCE_DIR = Path(__file__).parent / "resources"
RUNTIME_RESOURCE_DIR = data_dir("rocom") / "resources"
POKEDEX_DIR = RESOURCE_DIR / "pokedex"
FONT_DIR = RESOURCE_DIR / "fonts"
PET_ICON_DIR = RUNTIME_RESOURCE_DIR / "rocomicon"
SKILL_ICON_DIR = RUNTIME_RESOURCE_DIR / "skillicon"
CHARACTER_ICON_DIR = RUNTIME_RESOURCE_DIR / "characteristicicon"

RC_28 = load_font(FONT_DIR / "rocom_origin.ttf", 28)
RC_30 = load_font(FONT_DIR / "rocom_origin.ttf", 30)
RC_32 = load_font(FONT_DIR / "rocom_origin.ttf", 32)
RC_34 = load_font(FONT_DIR / "rocom_origin.ttf", 34)
RC_40 = load_font(FONT_DIR / "rocom_origin.ttf", 40)
RC_64 = load_font(FONT_DIR / "rocom_origin.ttf", 64)
RC_72 = load_font(FONT_DIR / "rocom_origin.ttf", 72)
SKILL_22 = load_font(FONT_DIR / "skill_origin.ttf", 22)
SKILL_32 = load_font(FONT_DIR / "skill_origin.ttf", 32)

TEXT_COLOR = (100, 92, 79)
WHITE = (255, 255, 255)
IMAGE_WIDTH = 1200
SECTION_HEIGHT = 70
TEXT_LINE_HEIGHT = 42
INFO_TEXT_X = 90
INFO_TEXT_MAX_WIDTH = 1020
FEATURE_TEXT_X = 230
FEATURE_TEXT_MAX_WIDTH = 880
FEATURE_ICON_SIZE = 121
FEATURE_TITLE_CENTER_OFFSET = 30
FEATURE_DESC_CENTER_OFFSET = 82
FEATURE_BOTTOM_PADDING = 20

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

TAG_X_ADD = [0, 132, 143]
TAG_FIELDS = ["attr_hp", "attr_atk", "attr_spatk", "attr_def", "attr_spdef", "attr_spd"]
TAG_TITLES = ["HP", "物攻", "魔攻", "物防", "魔防", "速度"]
EVOLUTION_X = {
    "1_0": 220,
    "2_0": 70,
    "2_1": 290,
    "3_0": 0,
    "3_1": 220,
    "3_2": 440,
}


_MEASURE_DRAW = ImageDraw.Draw(Image.new("RGB", (1, 1)))


def _asset(name: str) -> Image.Image:
    return Image.open(POKEDEX_DIR / name).convert("RGBA")


def _text_width(text: str, font: Any) -> float:
    return float(_MEASURE_DRAW.textlength(text, font=font))


def _wrap_text(text: str, font: Any, max_width: int) -> list[str]:
    """按像素宽度给中英文混排文本换行。"""
    if not text:
        return []
    lines: list[str] = []
    for part in str(text).splitlines():
        if not part:
            lines.append("")
            continue
        line = ""
        for char in part:
            candidate = line + char
            if line and _text_width(candidate, font) > max_width:
                lines.append(line.rstrip())
                line = char.lstrip()
            else:
                line = candidate
        if line:
            lines.append(line.rstrip())
    return lines


def _min_attr(value: int, title: str = "") -> int:
    base = value / 2 + 10
    factor = value / 100
    growth = 50
    if title == "HP":
        factor = factor * 2 + 1
        growth = 100
    return math.floor((base + factor * 60) * 0.9 + growth)


def _max_attr(value: int, title: str = "") -> int:
    base = (value + 30) / 2 + 10
    factor = (value + 30) / 100
    growth = 50
    if title == "HP":
        factor = factor * 2 + 1
        growth = 100
    return math.floor((base + factor * 60) * 1.2 + growth)


def _pet_name(pet: dict[str, Any]) -> str:
    name = str(pet.get("name") or "")
    form = str(pet.get("form") or "")
    return f"{name} - {form}" if form else name


def _load_pet_icon(icon: str, size: int, fallback_text: str = "") -> Image.Image:
    path = PET_ICON_DIR / f"{icon}.png"
    if path.is_file():
        return Image.open(path).convert("RGBA").resize((size, size))
    fallback = Image.new("RGBA", (size, size), (255, 255, 255, 0))
    draw = ImageDraw.Draw(fallback)
    draw.text((size // 2, size // 2), fallback_text or "暂无立绘", WHITE, RC_34, "mm")
    return fallback


def _load_skill_icon(name: str) -> Image.Image:
    path = SKILL_ICON_DIR / f"{name}.png"
    if path.is_file():
        return Image.open(path).convert("RGBA").resize((67, 67))
    icon = Image.open(POKEDEX_DIR / "icon.png").convert("RGBA").resize((67, 67))
    return icon


def _load_character_icon(name: str) -> Image.Image:
    path = CHARACTER_ICON_DIR / f"{name}.png"
    if path.is_file():
        return Image.open(path).convert("RGBA").resize((121, 121))
    icon = Image.open(POKEDEX_DIR / "icon.png").convert("RGBA").resize((121, 121))
    return icon


def _section(img: Image.Image, draw: ImageDraw.ImageDraw, y: int, title: str) -> int:
    rocom_title = _asset("a_title.png")
    img.paste(rocom_title, (68, y), rocom_title)
    draw.text((134, y + 30), title, WHITE, RC_28, "lm")
    return y + SECTION_HEIGHT


def _draw_type_badges(img: Image.Image, draw: ImageDraw.ImageDraw, pet: dict[str, Any]) -> None:
    mask_bar = _asset("mask_bar.png")
    for index, type_name in enumerate([str(item) for item in pet.get("unit_type") or []]):
        color = TYPE_COLORS.get(type_name, TYPE_COLORS["无"])
        type_img = Image.new("RGBA", (142, 38), color)
        type_icon_path = POKEDEX_DIR / f"{type_name}.png"
        if type_icon_path.is_file():
            icon = Image.open(type_icon_path).convert("RGBA").resize((42, 42))
            type_img.paste(icon, (-2, -2), icon)
        masked = Image.new("RGBA", (142, 38), (255, 255, 255, 0))
        masked.paste(type_img, (0, 0), mask_bar)
        type_draw = ImageDraw.Draw(masked)
        type_draw.text((91, 19), type_name, WHITE, RC_32, "mm")
        img.paste(masked, (150 * index + 90, 970), masked)


def _draw_stats(img: Image.Image, draw: ImageDraw.ImageDraw, pet: dict[str, Any]) -> None:
    rocom_title = _asset("a_title.png")
    table_img = _asset("table.png")
    tags_img = _asset("tags.png")
    img.paste(rocom_title, (565, 334), rocom_title)
    draw.text((631, 363), "精灵种族", WHITE, RC_28, "lm")
    img.paste(table_img, (550, 405), table_img)
    attr = pet.get("attribute") or {}
    x_num = 730
    y_num = 483
    for col in range(3):
        x_num += TAG_X_ADD[col]
        for row, field in enumerate(TAG_FIELDS):
            tag_x = x_num
            tag_y = y_num + row * 54
            img.paste(tags_img, (tag_x, tag_y), tags_img)
            value = int(attr.get(field) or 0)
            if col == 0:
                draw.text((tag_x - 95, tag_y + 22), TAG_TITLES[row], TEXT_COLOR, RC_34, "lm")
                draw.text((tag_x + 58, tag_y + 22), str(value), (240, 236, 225), RC_32, "mm")
            elif col == 1:
                draw.text(
                    (tag_x + 58, tag_y + 22),
                    str(_min_attr(value, TAG_TITLES[row])),
                    (240, 236, 225),
                    RC_32,
                    "mm",
                )
            else:
                draw.text(
                    (tag_x + 58, tag_y + 22),
                    str(_max_attr(value, TAG_TITLES[row])),
                    (240, 236, 225),
                    RC_32,
                    "mm",
                )


def _draw_evolution(img: Image.Image, draw: ImageDraw.ImageDraw, pet: dict[str, Any]) -> None:
    jinhua_bg = _asset("jinhua_bg.png")
    right_jinhua = _asset("right_jinhua.png")
    evolution = [item for item in pet.get("evolution_list") or [] if isinstance(item, dict)]
    evolution = evolution[:3]
    count = len(evolution)
    if not count:
        return
    base_x = 565
    base_y = 820
    for index, item in enumerate(evolution):
        icon_x = base_x + EVOLUTION_X.get(f"{count}_{index}", 220)
        img.paste(jinhua_bg, (icon_x, base_y), jinhua_bg)
        pet_icon = _load_pet_icon(str(item.get("icon") or ""), 150, str(item.get("name") or ""))
        img.paste(pet_icon, (icon_x - 5, base_y + 10), pet_icon)
        draw.text(
            (icon_x + 70, base_y + 220),
            str(item.get("name") or ""),
            (60, 60, 60),
            SKILL_22,
            "mm",
        )
        if count > 1 and index > 0:
            img.paste(right_jinhua, (icon_x - 54, 863), right_jinhua)
            draw.text(
                (icon_x - 54, 905),
                str(item.get("level") or ""),
                TEXT_COLOR,
                RC_28,
                "mm",
            )


def _draw_skill_group(
    img: Image.Image,
    draw: ImageDraw.ImageDraw,
    y: int,
    title: str,
    skills: list[dict[str, Any]],
) -> int:
    if not skills:
        return y
    y = _section(img, draw, y, title)
    skill_bg = _asset("skill_bg.png")
    cost_star = _asset("star.png")
    for index, skill in enumerate(skills):
        row = index // 5
        col = index - row * 5
        family = str(skill.get("families") or "无")
        color = TYPE_COLORS.get(family, TYPE_COLORS["无"])
        card = Image.new("RGBA", (207, 99), color)
        temp = Image.new("RGBA", (207, 99), (255, 255, 255, 0))
        temp.paste(card, (0, 0), skill_bg)
        skill_icon = _load_skill_icon(str(skill.get("name") or ""))
        temp.paste(skill_icon, (15, 16), skill_icon)
        family_icon_path = POKEDEX_DIR / f"{family}.png"
        if family_icon_path.is_file():
            family_icon = Image.open(family_icon_path).convert("RGBA").resize((45, 45))
            temp.paste(family_icon, (-5, -5), family_icon)
        temp_draw = ImageDraw.Draw(temp)
        temp_draw.text((94, 35), str(skill.get("name") or "")[:6], WHITE, SKILL_22, "lm")
        temp.paste(cost_star, (92, 52), cost_star)
        temp_draw.text((120, 65), str(skill.get("cost") or "0"), WHITE, SKILL_22, "lm")
        img.paste(temp, (208 * col + 82, row * 99 + y), temp)
    return y + (math.ceil(len(skills) / 5) * 99) + 10


def _skill_group_height(skills: list[dict[str, Any]]) -> int:
    if not skills:
        return 0
    return SECTION_HEIGHT + math.ceil(len(skills) / 5) * 99 + 10


async def render_pokedex_image(pet: dict[str, Any], pet_id: str) -> bytes:
    """按照 RocomUID 图鉴布局生成图片。"""
    level_skills = [item for item in pet.get("level_skill_list") or [] if isinstance(item, dict)]
    blood_skills = [item for item in pet.get("blood_skill_list") or [] if isinstance(item, dict)]
    machine_skills = [
        item for item in pet.get("machine_skill_list") or [] if isinstance(item, dict)
    ]
    feature = pet.get("feature") or {}
    feature_name = str(feature.get("name") or "")
    feature_lines = _wrap_text(str(feature.get("desc") or ""), SKILL_32, FEATURE_TEXT_MAX_WIDTH)
    desc_lines = _wrap_text(str(pet.get("description") or ""), SKILL_32, INFO_TEXT_MAX_WIDTH)

    info_body_height = (
        len(desc_lines) * TEXT_LINE_HEIGHT + (TEXT_LINE_HEIGHT if pet.get("egg_group") else 0) + 15
    )
    feature_line_count = len(feature_lines) or 1
    feature_body_height = max(
        FEATURE_ICON_SIZE + FEATURE_BOTTOM_PADDING,
        FEATURE_DESC_CENTER_OFFSET + feature_line_count * TEXT_LINE_HEIGHT + FEATURE_BOTTOM_PADDING,
    )
    bg_height = 1030
    bg_height += SECTION_HEIGHT + info_body_height
    bg_height += SECTION_HEIGHT + feature_body_height + 15
    bg_height += _skill_group_height(level_skills)
    bg_height += _skill_group_height(blood_skills)
    bg_height += _skill_group_height(machine_skills)
    bg_height += 40

    img = Image.open(POKEDEX_DIR / "bg.jpg").convert("RGB").resize((IMAGE_WIDTH, bg_height))
    title_img = _asset("title.png")
    pet_bg_mask = _asset("pet_bg.png").resize((575, 575))
    img.paste(title_img, (0, 0), title_img)
    draw = ImageDraw.Draw(img)
    draw.text((600, 96), "精灵图鉴", WHITE, RC_72, "mm")
    draw.text((600, 260), _pet_name(pet), TEXT_COLOR, RC_64, "mm")

    first_type = str((pet.get("unit_type") or ["无"])[0])
    pet_bg = Image.new("RGBA", (575, 575), TYPE_COLORS.get(first_type, TYPE_COLORS["无"]))
    img.paste(pet_bg, (-6, 359), pet_bg_mask)
    pet_icon = _load_pet_icon(str(pet.get("icon") or ""), 552, str(pet.get("name") or ""))
    img.paste(pet_icon, (0, 371), pet_icon)

    _draw_stats(img, draw, pet)
    _draw_evolution(img, draw, pet)
    _draw_type_badges(img, draw, pet)

    y = 1030
    y = _section(img, draw, y, "精灵信息")
    if pet.get("egg_group"):
        draw.text(
            (INFO_TEXT_X, y),
            f"蛋组：{' '.join(str(item) for item in pet.get('egg_group') or [])}",
            TEXT_COLOR,
            RC_34,
            "lm",
        )
        y += TEXT_LINE_HEIGHT
    for line in desc_lines:
        draw.text((INFO_TEXT_X, y), line, TEXT_COLOR, SKILL_32, "lm")
        y += TEXT_LINE_HEIGHT
    y += 15

    y = _section(img, draw, y, "精灵特性")
    skill_mask = _asset("skill_mask.png")
    tx_img = _load_character_icon(feature_name)
    img.paste(tx_img, (90, y), skill_mask)
    feature_body_y = y
    draw.text(
        (FEATURE_TEXT_X, feature_body_y + FEATURE_TITLE_CENTER_OFFSET),
        feature_name or "暂无特性",
        (0, 0, 0),
        RC_40,
        "lm",
    )
    line_y = FEATURE_DESC_CENTER_OFFSET
    for line in feature_lines or ["暂无特性说明"]:
        draw.text((FEATURE_TEXT_X, feature_body_y + line_y), line, TEXT_COLOR, SKILL_32, "lm")
        line_y += TEXT_LINE_HEIGHT
    y = feature_body_y + feature_body_height + 15

    y = _draw_skill_group(img, draw, y, "等级技能", level_skills)
    y = _draw_skill_group(img, draw, y, "血脉技能", blood_skills)
    y = _draw_skill_group(img, draw, y, "技能石技能", machine_skills)

    output = BytesIO()
    img.save(output, format="PNG")
    return output.getvalue()
