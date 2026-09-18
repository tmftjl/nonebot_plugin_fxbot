"""远行商人图片渲染。"""

from __future__ import annotations

import math
import asyncio
from io import BytesIO
from pathlib import Path
from functools import lru_cache

from PIL import Image, ImageDraw

from .client import MerchantProduct, MerchantSnapshot
from ...utils.http import get_shared_async_client
from ...utils.fonts import load_font

RESOURCE_DIR = Path(__file__).parent / "resources"
TEXTURE_DIR = RESOURCE_DIR / "merchant"
FONT_DIR = RESOURCE_DIR / "fonts"

FONT_TITLE = load_font(FONT_DIR / "rocom_origin.ttf", 40)
FONT_META = load_font(FONT_DIR / "skill_origin.ttf", 26)
FONT_SMALL = load_font(FONT_DIR / "skill_origin.ttf", 18)

RECOMMEND_NAMES = {"炫彩精灵蛋", "棱镜球", "国王球"}
_ICON_CACHE: dict[str, Image.Image] = {}


@lru_cache(maxsize=8)
def _load_texture(name: str, mode: str) -> Image.Image:
    """加载并缓存固定渲染素材。"""
    return Image.open(TEXTURE_DIR / name).convert(mode)


async def _fetch_icon(url: str) -> Image.Image | None:
    if not url:
        return None
    if url in _ICON_CACHE:
        return _ICON_CACHE[url]
    try:
        client = await get_shared_async_client()
        response = await client.get(url, timeout=10.0, follow_redirects=True)
        response.raise_for_status()
        icon = Image.open(BytesIO(response.content)).convert("RGBA")
        _ICON_CACHE[url] = icon
        if len(_ICON_CACHE) > 256:
            _ICON_CACHE.pop(next(iter(_ICON_CACHE)))
        return icon
    except Exception:
        return None


def _fit_icon(icon: Image.Image, size: int = 145) -> Image.Image:
    width, height = icon.size
    if width <= 0 or height <= 0:
        return icon.resize((size, size))
    scale = size / max(width, height)
    return icon.resize((max(1, int(width * scale)), max(1, int(height * scale))))


def _text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    value: str,
    fill: tuple[int, int, int],
    font,
    anchor: str,
) -> None:
    draw.text(xy, value, fill, font, anchor)


def _time_label(product: MerchantProduct, snapshot: MerchantSnapshot) -> str:
    if product.starttime or product.endtime:
        return f"{product.starttime} ~ {product.endtime}".strip(" ~")
    return f"下次刷新 {snapshot.next_refresh}" if snapshot.next_refresh else "远行商人商品"


async def render_merchant_image(snapshot: MerchantSnapshot) -> bytes:
    """按照 RocomUID 远行商人布局生成图片。"""
    products = snapshot.products
    prop_num = len(products)
    prop_height = max(556, math.ceil(max(prop_num, 1) / 2) * 206)
    img_height = prop_height + 474

    badge = _load_texture("badge.png", "RGBA")
    banner = _load_texture("banner.png", "RGBA")
    susume = _load_texture("susume.png", "RGBA")
    top_img = _load_texture("bg_top.jpg", "RGB")
    footer_img = _load_texture("bg_footer.jpg", "RGB")
    products = products[:8]

    img = Image.new("RGBA", (1000, img_height))
    img.paste(top_img, (0, 0))
    bg_center = _load_texture("bg_center.jpg", "RGB").resize((1000, prop_height))
    img.paste(bg_center, (0, 321))
    img.paste(footer_img, (0, prop_height + 321))
    img.paste(banner, (196, 252), banner)
    draw = ImageDraw.Draw(img)

    _text(draw, (285, 270), f"当前商品 {prop_num}", (255, 255, 255), FONT_META, "mm")
    round_label = f"第 {snapshot.round_no}/4 轮" if snapshot.round_no else "远行商人"
    _text(draw, (500, 270), round_label, (255, 255, 255), FONT_META, "mm")
    _text(
        draw,
        (706, 270),
        f"剩余 {snapshot.remaining_time or '--'}",
        (255, 255, 255),
        FONT_META,
        "mm",
    )

    if not products:
        _text(draw, (500, 520), "暂未解析到商品明细", (255, 255, 63), FONT_TITLE, "mm")

    start_height = 277
    icons = await asyncio.gather(*(_fetch_icon(product.image) for product in products))
    for index, (product, icon) in enumerate(zip(products, icons, strict=True)):
        row = index // 2
        col = index - row * 2
        prop_img = Image.new("RGBA", (512, 256), (255, 255, 255, 0))
        prop_img.paste(badge, (0, 0), badge)

        if icon is not None:
            icon = _fit_icon(icon)
            icon_x = 131 - icon.size[0] // 2
            icon_y = 128 - icon.size[1] // 2
            prop_img.paste(icon, (icon_x, icon_y), icon)

        prop_draw = ImageDraw.Draw(prop_img)
        _text(prop_draw, (210, 116), product.name[:12], (255, 255, 63), FONT_TITLE, "lm")
        _text(
            prop_draw,
            (210, 152),
            _time_label(product, snapshot)[:26],
            (198, 222, 246),
            FONT_SMALL,
            "lm",
        )
        if product.name in RECOMMEND_NAMES:
            prop_img.paste(susume, (371, 37), susume)
        img.paste(prop_img, (453 * col + 14, row * 206 + start_height), prop_img)

    output = BytesIO()
    img.convert("RGB").save(output, format="PNG")
    return output.getvalue()
