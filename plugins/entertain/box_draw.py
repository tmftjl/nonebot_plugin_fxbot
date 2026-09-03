"""开盒信息图片绘制。"""

from __future__ import annotations

import io
import random
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw

from ...utils.fonts import get_shared_font_path, load_font

try:
    import emoji
except Exception:  # pragma: no cover
    emoji = None

RESOURCE_DIR = Path(__file__).resolve().parent / "resource"
FONT_PATH = get_shared_font_path()
EMOJI_FONT_PATH = RESOURCE_DIR / "NotoColorEmoji.ttf"

FONT_SIZE = 35
TEXT_PADDING = 10
BORDER_THICKNESS = 10
BORDER_COLOR_RANGE = (64, 255)
CORNER_RADIUS = 30

FONT = load_font(FONT_PATH, FONT_SIZE)
EMOJI_FONT = load_font(EMOJI_FONT_PATH, FONT_SIZE)


def create_image(avatar: bytes, reply: list[str]) -> bytes:
    """按旧版排版生成开盒信息图片。"""
    text = "\n".join(reply)
    temp_img = Image.new("RGBA", (1, 1))
    temp_draw = ImageDraw.Draw(temp_img)
    if emoji is not None:
        measure_text = "".join(
            "一" if getattr(emoji, "is_emoji", None) and emoji.is_emoji(ch) else ch
            for ch in text
        )
    else:
        measure_text = text
    text_bbox = temp_draw.textbbox((0, 0), measure_text, font=FONT)
    text_width = int(text_bbox[2] - text_bbox[0])
    text_height = max(1, int(text_bbox[3] - text_bbox[1]))
    image_height = text_height + 2 * TEXT_PADDING

    try:
        avatar_image = Image.open(BytesIO(avatar)).convert("RGBA")
    except Exception:
        avatar_image = Image.new(
            "RGBA",
            (max(1, text_height), max(1, text_height)),
            color=(240, 240, 240, 255),
        )
    avatar_image = avatar_image.resize((max(1, text_height), max(1, text_height)))

    image_width = avatar_image.width + text_width + 2 * TEXT_PADDING
    image = Image.new("RGBA", (image_width, image_height), color=(255, 255, 255, 255))

    mask = Image.new("L", (avatar_image.width, avatar_image.height), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle(
        [(0, 0), (avatar_image.width, avatar_image.height)], CORNER_RADIUS, fill=255
    )
    avatar_image.putalpha(mask)
    image.paste(avatar_image, (0, (image_height - avatar_image.height) // 2), mask)
    _draw_multi(image, text, avatar_image.width + TEXT_PADDING, TEXT_PADDING)

    border_color = (
        random.randint(*BORDER_COLOR_RANGE),
        random.randint(*BORDER_COLOR_RANGE),
        random.randint(*BORDER_COLOR_RANGE),
    )
    output = Image.new(
        mode="RGBA",
        size=(image_width + BORDER_THICKNESS * 2, image_height + BORDER_THICKNESS * 2),
        color=border_color,
    )
    output.paste(image, (BORDER_THICKNESS, BORDER_THICKNESS))
    buffer = io.BytesIO()
    output.save(buffer, format="PNG")
    return buffer.getvalue()


def _draw_multi(
    image: Image.Image, text: str, text_x: int = 10, text_y: int = 10
) -> Image.Image:
    """逐字绘制文本，保持旧版随机颜色和 emoji 字体。"""
    draw = ImageDraw.Draw(image)
    current_y = text_y
    for line in text.split("\n"):
        line_color = (
            random.randint(0, 128),
            random.randint(0, 128),
            random.randint(0, 128),
            random.randint(240, 255),
        )
        current_x = text_x
        for char in line:
            is_emoji = False
            if emoji is not None:
                try:
                    is_emoji = char in getattr(emoji, "EMOJI_DATA", {})
                except Exception:
                    is_emoji = False
            font = EMOJI_FONT if is_emoji else FONT
            draw.text(
                (current_x, current_y + (10 if is_emoji else 0)),
                char,
                font=font,
                fill=line_color,
            )
            bbox = font.getbbox(char)
            current_x += bbox[2] - bbox[0]
        current_y += 40
    return image
