"""字体加载辅助函数。"""

from __future__ import annotations

from pathlib import Path

from PIL import ImageFont


def load_font(path: str | Path | None, size: int):
    """加载指定字体，失败时回退到 PIL 默认字体。"""
    if path:
        try:
            font_path = Path(path)
            if font_path.is_file():
                return ImageFont.truetype(str(font_path), size)
        except Exception:
            pass
    try:
        return ImageFont.truetype("arial.ttf", size)
    except Exception:
        return ImageFont.load_default()
