"""字体加载辅助函数。"""

from __future__ import annotations

from pathlib import Path

from PIL import ImageDraw, ImageFont

from .paths import package_root


def get_shared_font_path(name: str = "FZB.ttf") -> Path:
    """获取项目共享字体路径（resources/fonts/）。"""
    return package_root() / "resources" / "fonts" / name


def get_jp_fallback_font_path() -> Path:
    """获取日文回退字体路径（MS Gothic，覆盖假名和和制汉字）。"""
    return package_root() / "resources" / "fonts" / "MSGothic.ttc"


def get_matplotlib_font(name: str = "FZB.ttf") -> str:
    """注册共享字体到 matplotlib 并返回 family name。"""
    from matplotlib.font_manager import FontProperties, fontManager

    font_path = get_shared_font_path(name)
    fontManager.addfont(str(font_path))
    return FontProperties(fname=str(font_path)).get_name()


def load_font(path: str | Path, size: int):
    """加载指定字体文件并返回 PIL ImageFont。"""
    return ImageFont.truetype(str(path), size)


def load_fallback_font_pair(primary_name: str = "FZB.ttf", size: int = 26):
    """加载主字体和日文回退字体对，返回 (primary_font, fallback_font)。"""
    primary = load_font(get_shared_font_path(primary_name), size)
    fallback = load_font(get_jp_fallback_font_path(), size)
    return primary, fallback


def _has_glyph(font: ImageFont.FreeTypeFont, char: str) -> bool:
    """判断字体是否包含某个字符的 glyph。

    有些字体对不支持的字形会返回宽度非零但高度为零的空遮罩（如 FZB 对假名），
    因此必须同时检查 bbox 是否有效。
    """
    try:
        mask = font.getmask(char)
        if mask is None:
            return False
        bbox = mask.getbbox()
        if bbox is None:
            return False
        # bbox 为 (left, top, right, bottom)，有效字形应有正面积
        return bbox[2] > bbox[0] and bbox[3] > bbox[1]
    except Exception:
        return False


def _segment_by_glyph(
    text: str,
    primary: ImageFont.FreeTypeFont,
    fallback: ImageFont.FreeTypeFont,
) -> list[tuple[str, ImageFont.FreeTypeFont]]:
    """将文本按字体覆盖范围分割为 (segment_text, font) 列表。"""
    segments: list[tuple[str, ImageFont.FreeTypeFont]] = []
    if not text:
        return segments
    chars: list[str] = []
    current_font: ImageFont.FreeTypeFont | None = None
    for char in text:
        font = primary if _has_glyph(primary, char) else fallback
        if font != current_font:
            if chars:
                segments.append(("".join(chars), current_font))
                chars = []
            current_font = font
        chars.append(char)
    if chars:
        segments.append(("".join(chars), current_font))
    return segments


def textlength_with_fallback(
    text: str,
    primary: ImageFont.FreeTypeFont,
    fallback: ImageFont.FreeTypeFont,
) -> float:
    """使用回退字体计算文本宽度。"""
    total = 0.0
    for segment, font in _segment_by_glyph(text, primary, fallback):
        total += font.getlength(segment)
    return total


def draw_text_with_fallback(
    draw: ImageDraw.ImageDraw,
    xy: tuple[float, float],
    text: str,
    primary: ImageFont.FreeTypeFont,
    fallback: ImageFont.FreeTypeFont,
    fill: tuple[int, int, int] | str | None = None,
    **kwargs,
) -> None:
    """使用回退字体绘制文本（主字体不包含的字形会使用日文后备字体）。"""
    x, y = xy
    for segment, font in _segment_by_glyph(text, primary, fallback):
        draw.text((x, y), segment, fill=fill, font=font, **kwargs)
        x += font.getlength(segment)


def textbbox_with_fallback(
    draw: ImageDraw.ImageDraw,
    text: str,
    primary: ImageFont.FreeTypeFont,
    fallback: ImageFont.FreeTypeFont,
    **kwargs,
) -> tuple[float, float, float, float]:
    """使用回退字体计算 textbbox，模拟 anchor 行为。"""
    width = textlength_with_fallback(text, primary, fallback)
    # 取主字体的 ascent/descent 作为 baseline；fallback 字号相同一般一致
    ascent, descent = primary.getmetrics()
    left, top, _right, _bottom = draw.textbbox((0, 0), text, font=primary, **kwargs)
    return (left, top, left + width, top + ascent + descent)


def truncate_text_with_fallback(
    text: str,
    max_width: float,
    primary: ImageFont.FreeTypeFont,
    fallback: ImageFont.FreeTypeFont,
    suffix: str = "…",
) -> str:
    """按最大宽度截断文本（使用回退字体计宽），超出部分用省略号替代。"""
    if textlength_with_fallback(text, primary, fallback) <= max_width:
        return text
    suffix_w = textlength_with_fallback(suffix, primary, fallback)
    available = max_width - suffix_w
    if available <= 0:
        return suffix
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if textlength_with_fallback(text[:mid], primary, fallback) <= available:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo] + suffix
