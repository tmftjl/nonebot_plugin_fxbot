"""帮助图片渲染。"""

from __future__ import annotations

import asyncio
import base64
import io
from pathlib import Path
from typing import Any

from nonebot import get_driver, logger
from PIL import Image, ImageDraw

from ...utils.fonts import get_shared_font_path, load_font

RES_DIR = Path(__file__).parent / "resources"
_PW = None
_BROWSER = None
_RENDER_SEM = asyncio.Semaphore(2)


def _mime(path: Path) -> str:
    """根据文件后缀推断 MIME。"""
    suffix = path.suffix.lower()
    if suffix == ".png":
        return "image/png"
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".webp":
        return "image/webp"
    if suffix == ".woff":
        return "font/woff"
    if suffix == ".ttf":
        return "font/ttf"
    return "application/octet-stream"


def _data_uri(path: Path, mime: str | None = None) -> str:
    """把资源文件转为 data URI。"""
    return f"data:{mime or _mime(path)};base64,{base64.b64encode(path.read_bytes()).decode()}"


def _inline_css(icon: Path | None = None) -> str:
    """内联旧版帮助图 CSS 和字体资源。"""
    common_css = (RES_DIR / "common" / "common.css").read_text(encoding="utf-8")
    help_css = (RES_DIR / "help" / "index.css").read_text(encoding="utf-8")

    replacements = {
        "./font/FZB.woff": _data_uri(RES_DIR / "common" / "font" / "FZB.woff"),
        "./font/FZB.ttf": _data_uri(RES_DIR / "common" / "font" / "FZB.ttf"),
        "./font/NZBZ.woff": _data_uri(RES_DIR / "common" / "font" / "NZBZ.woff"),
        "./font/NZBZ.ttf": _data_uri(RES_DIR / "common" / "font" / "NZBZ.ttf"),
    }
    for old, new in replacements.items():
        common_css = common_css.replace(old, new)
    icon_path = icon if icon and icon.is_file() else RES_DIR / "help" / "icon.png"
    help_css = help_css.replace("icon.png", _data_uri(icon_path))
    return f"<style>{common_css}\n{help_css}</style>"


def _build_html(
    title: str,
    sub_title: str,
    groups: list[dict[str, Any]],
    col_count: int,
    background: Path,
    footer: str | None = None,
    icon: Path | None = None,
) -> str:
    """构造旧版帮助图 HTML。"""

    def icon_css(index: int) -> str:
        if not index:
            return "display:none"
        x = (index - 1) % 10
        y = (index - x - 1) // 10
        return f"background-position:-{x * 50}px -{y * 50}px"

    def render_group(group: dict[str, Any]) -> str:
        items = group.get("list", []) or []
        rows: list[str] = []
        step = max(col_count, 1)
        for i in range(0, len(items), step):
            chunk = items[i : i + step]
            cells: list[str] = []
            for item in chunk:
                css = icon_css(int(item.get("icon") or 0))
                cells.append(
                    f"<div class='td'><span class='help-icon' style='{css}'></span>"
                    f"<strong class='help-title'>{item.get('title', '')}</strong>"
                    f"<span class='help-desc'>{item.get('desc', '')}</span></div>"
                )
            while len(cells) < step:
                cells.append("<div class='td'></div>")
            rows.append(f"<div class='tr'>{''.join(cells)}</div>")
        table = f"<div class='help-table'>{''.join(rows)}</div>" if rows else ""
        return f"<div class='cont-box'><div class='help-group'>{group.get('group', '')}</div>{table}</div>"

    bg_uri = _data_uri(background)
    footer_text = footer if footer and str(footer).strip() else "Created by dggb | Rendered by Playwright"
    return f"""
    <!doctype html>
    <html>
      <head>
        <meta charset='utf-8'/>
        {_inline_css(icon)}
        <style>
          .container {{ background: url('{bg_uri}') center !important; background-size: cover !important; }}
          .help-table .td, .help-table .th {{ width: {100 / max(col_count, 1):.6f}% !important; }}
        </style>
      </head>
      <body>
        <div class='container'>
          <div class='info-box'>
            <div class='head-box'>
              <div class='title'>{title}</div>
              <div class='label'>{sub_title}</div>
            </div>
          </div>
          {''.join(render_group(group) for group in groups)}
          <div class='copyright'>{footer_text}</div>
        </div>
      </body>
    </html>
    """


async def render_help_image(
    title: str,
    sub_title: str,
    groups: list[dict[str, Any]],
    col_count: int = 3,
    scale: float = 1.2,
    footer: str | None = None,
    background: Path | None = None,
    icon: Path | None = None,
) -> bytes:
    """按旧版排版渲染帮助图。"""

    def fallback() -> bytes:
        image = Image.new("RGB", (1200, 800), (245, 247, 250))
        draw = ImageDraw.Draw(image)
        font_title = load_font(get_shared_font_path(), 48)
        font_sub = load_font(get_shared_font_path(), 28)
        draw.text((50, 60), title, fill=(30, 30, 30), font=font_title)
        draw.text((50, 120), sub_title, fill=(80, 80, 80), font=font_sub)
        y = 180
        for group in groups[:10]:
            draw.text((50, y), f"- {group.get('group', '')}", fill=(40, 40, 40), font=font_sub)
            y += 40
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()

    try:
        from playwright.async_api import async_playwright
    except Exception:
        return fallback()

    img_dir = RES_DIR / "help" / "imgs"
    backgrounds = [path.name for path in img_dir.iterdir() if path.is_file()]
    default_bg = img_dir / (backgrounds[0] if backgrounds else "default.jpg")
    bg = background if background and background.is_file() else default_bg
    html = _build_html(title, sub_title, groups, max(col_count, 1), bg, footer, icon)

    async def ensure_browser():
        global _PW, _BROWSER
        if _BROWSER is not None:
            return _BROWSER
        _PW = await async_playwright().start()
        _BROWSER = await _PW.chromium.launch()
        return _BROWSER

    try:
        async with _RENDER_SEM:
            browser = await ensure_browser()
            page = await browser.new_page(device_scale_factor=scale)
            try:
                await asyncio.wait_for(page.set_content(html, wait_until="load"), timeout=15.0)
                element = await page.query_selector(".container")
                if element:
                    return await asyncio.wait_for(element.screenshot(type="png"), timeout=15.0)
                return await asyncio.wait_for(page.screenshot(type="png", full_page=True), timeout=15.0)
            finally:
                await page.close()
    except Exception as exc:
        logger.warning(f"[help][renderer] Playwright 渲染失败，回退到 PIL: {exc}")
        return fallback()


@get_driver().on_shutdown
async def _shutdown_renderer() -> None:
    """关闭帮助图渲染器。"""
    global _PW, _BROWSER
    try:
        if _BROWSER is not None:
            await _BROWSER.close()
    finally:
        _BROWSER = None
    try:
        if _PW is not None:
            await _PW.stop()
    finally:
        _PW = None
