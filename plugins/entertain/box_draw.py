"""开盒信息图片绘制。"""

from __future__ import annotations

import io
import random
from io import BytesIO


def _load_default_font():
    """加载 Pillow 默认字体。"""
    try:
        from PIL import ImageFont

        return ImageFont.load_default()
    except Exception:
        return None


def create_image(avatar: bytes, reply: list[str]) -> bytes:
    """生成开盒信息图片。"""
    try:
        from PIL import Image, ImageDraw
    except Exception as exc:
        raise RuntimeError("Pillow 未安装，无法生成开盒图片") from exc

    font = _load_default_font()
    text = "\n".join(reply)
    lines = text.splitlines() or ["无信息"]
    row_height = 28
    avatar_size = max(120, min(280, row_height * len(lines)))
    width = 280 + max(320, max((len(line) for line in lines), default=10) * 16)
    height = max(avatar_size + 40, len(lines) * row_height + 40)

    image = Image.new("RGBA", (width, height), color=(255, 255, 255, 255))
    draw = ImageDraw.Draw(image)

    try:
        avatar_image = Image.open(BytesIO(avatar)).convert("RGBA").resize((avatar_size, avatar_size))
    except Exception:
        avatar_image = Image.new("RGBA", (avatar_size, avatar_size), color=(240, 240, 240, 255))

    mask = Image.new("L", (avatar_size, avatar_size), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle([(0, 0), (avatar_size, avatar_size)], radius=24, fill=255)
    image.paste(avatar_image, (20, (height - avatar_size) // 2), mask)

    x = avatar_size + 45
    y = 24
    for line in lines:
        color = (
            random.randint(0, 128),
            random.randint(0, 128),
            random.randint(0, 128),
            255,
        )
        draw.text((x, y), line, font=font, fill=color)
        y += row_height

    border_color = (
        random.randint(64, 255),
        random.randint(64, 255),
        random.randint(64, 255),
        255,
    )
    output = Image.new("RGBA", (width + 20, height + 20), color=border_color)
    output.paste(image, (10, 10))
    buffer = io.BytesIO()
    output.save(buffer, format="PNG")
    return buffer.getvalue()
