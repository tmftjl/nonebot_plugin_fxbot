"""字体加载辅助函数。"""

from __future__ import annotations

from pathlib import Path

from PIL import ImageFont

from .paths import package_root


def get_shared_font_path(name: str = "FZB.ttf") -> Path:
    """获取项目共享字体路径（resources/fonts/）。"""
    return package_root() / "resources" / "fonts" / name


def get_matplotlib_font(name: str = "FZB.ttf") -> str:
    """注册共享字体到 matplotlib 并返回 family name。"""
    from matplotlib.font_manager import FontProperties, fontManager

    font_path = get_shared_font_path(name)
    fontManager.addfont(str(font_path))
    return FontProperties(fname=str(font_path)).get_name()


def load_font(path: str | Path, size: int):
    """加载指定字体文件并返回 PIL ImageFont。"""
    return ImageFont.truetype(str(path), size)
