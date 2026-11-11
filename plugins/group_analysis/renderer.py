"""群聊日报长图渲染（初音未来主题，移植自 astrbot_plugin_qq_group_daily_analysis，MIT）。"""

from __future__ import annotations

import io
import html as html_lib
import base64
import asyncio
from typing import Any
from pathlib import Path
from datetime import datetime
from functools import lru_cache

from PIL import Image, ImageDraw
from nonebot import logger

from ...utils.tz import SHANGHAI_TZ
from ...utils.fonts import load_font, get_shared_font_path

_RENDER_SEM = asyncio.Semaphore(2)
_ASSETS = Path(__file__).with_name("resources") / "miku"
_TOPIC_IMAGES = (
    "retouch_2026032810150449.png",
    "retouch_2026032810143720.png",
    "retouch_2026032810151717.png",
    "retouch_2026032810153327.png",
    "retouch_2026032810145078.png",
)
_CHART_COLORS = (
    "#66D1C7",
    "#6FD3C6",
    "#79D5C4",
    "#85D8C3",
    "#93DAC1",
    "#A3DDBF",
    "#B4E0BD",
    "#C7E2BA",
    "#DBE5B8",
    "#EFE8B5",
    "#FFEAB3",
    "#FFDF9E",
    "#FFD38B",
    "#FFC67A",
    "#FFB96D",
    "#FFAC62",
    "#FFA059",
    "#FF9552",
    "#FF8B4D",
    "#FF834B",
    "#FF7D4D",
    "#FF7752",
    "#FF745A",
    "#FF7365",
)
_DIM_COLORS = ("var(--miku-base)", "var(--miku-pink-light)", "var(--miku-light)", "var(--miku-yellow)", "#80CBC4")
_ICONS = {
    "msgs": (
        "<path d='M22 17a2 2 0 0 1-2 2H6.828a2 2 0 0 0-1.414.586l-2.202 2.202"
        "A.71.71 0 0 1 2 21.286V5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2z'/>"
    ),
    "users": (
        "<path d='M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2'/>"
        "<path d='M16 3.128a4 4 0 0 1 0 7.744'/>"
        "<path d='M22 21v-2a4 4 0 0 0-3-3.87'/><circle cx='9' cy='7' r='4'/>"
    ),
    "smile": (
        "<path d='M15 10V9'/><path d='M16.472 15a6 6 0 01-8.943 0'/><path d='M9 10V9'/><circle cx='12' cy='12' r='10'/>"
    ),
    "type": ("<path d='M12 4v16'/><path d='M4 7V5a1 1 0 0 1 1-1h14a1 1 0 0 1 1 1v2'/><path d='M9 20h6'/>"),
    "trending": "<path d='M16 7h6v6'/><path d='m22 7-8.5 8.5-5-5L2 17'/>",
    "chart": "<path d='M3 3v16a2 2 0 0 0 2 2h16'/><path d='m19 9-5 5-4-4-3 3'/>",
    "quote": (
        "<path d='M16 3a2 2 0 0 0-2 2v6a2 2 0 0 0 2 2 1 1 0 0 1 1 1v1a2 2 0 0 1-2 2"
        " 1 1 0 0 0-1 1v2a1 1 0 0 0 1 1 6 6 0 0 0 6-6V5a2 2 0 0 0-2-2z'/>"
        "<path d='M5 3a2 2 0 0 0-2 2v6a2 2 0 0 0 2 2 1 1 0 0 1 1 1v1a2 2 0 0 1-2 2"
        " 1 1 0 0 0-1 1v2a1 1 0 0 0 1 1 6 6 0 0 0 6-6V5a2 2 0 0 0-2-2z'/>"
    ),
    "award": (
        "<path d='m15.477 12.89 1.515 8.526a.5.5 0 0 1-.81.47l-3.58-2.687"
        "a1 1 0 0 0-1.197 0l-3.586 2.686a.5.5 0 0 1-.81-.469l1.514-8.526'/>"
        "<circle cx='12' cy='8' r='6'/>"
    ),
    "member": "<path d='m12 8-9.04 9.06a2.82 2.82 0 1 0 3.98 3.98L16 12'/><circle cx='17' cy='7' r='5'/>",
    "music": "<path d='M9 18V5l12-2v13'/><circle cx='6' cy='18' r='3'/><circle cx='18' cy='16' r='3'/>",
    "clock": "<circle cx='12' cy='12' r='10'/><path d='M12 6v6l4 2'/>",
}
_QUOTE_WATERMARK = (
    "<svg viewBox='0 0 24 24' fill='var(--miku-base)'>"
    "<path d='M3 21c3 0 7-1 7-8V5c0-1.25-.75-2-2-2H4c-1.25 0-2 .75-2 2v5c0 1.25.75 2 2 2h3c0 4-4 6-4 6Z"
    "m11 0c3 0 7-1 7-8V5c0-1.25-.75-2-2-2h-4c-1.25 0-2 .75-2 2v5c0 1.25.75 2 2 2h3c0 4-4 6-4 6Z'/></svg>"
)


def _escape(value: Any) -> str:
    return html_lib.escape(str(value or ""))


def _icon(name: str, style: str = "width: 24px; height: 24px; color: var(--miku-dark);") -> str:
    return (
        f"<svg viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' "
        f"stroke-linecap='round' stroke-linejoin='round' style='{style}'>{_ICONS[name]}</svg>"
    )


@lru_cache(maxsize=1)
def _font_faces() -> str:
    body = base64.b64encode(get_shared_font_path().read_bytes()).decode()
    title = base64.b64encode((_ASSETS / "cjkFonts-AllSeto-Title-v1.11.woff2").read_bytes()).decode()
    return (
        f"@font-face{{font-family:'FZB';src:url(data:font/ttf;base64,{body}) format('truetype');}}"
        f"@font-face{{font-family:'AllSeto';src:url(data:font/woff2;base64,{title}) format('woff2');}}"
    )


@lru_cache(maxsize=None)
def _image(name: str) -> str:
    return "data:image/png;base64," + base64.b64encode((_ASSETS / name).read_bytes()).decode()


@lru_cache(maxsize=1)
def _style() -> str:
    css = (_ASSETS / "report.css").read_text(encoding="utf-8")
    peak = _image("retouch_2026032802083201.png")
    return f"<style>{_font_faces()}\n{css}\n:root{{--miku-peak-activity-image:url({peak});}}</style>"


def _stat_box(icon: str, label: str, value: int) -> str:
    return (
        "<div class='stat-box'>"
        "<div class='stat-head'>"
        f"{_icon(icon, 'width: 18px; height: 18px; color: var(--miku-base);')}"
        f"<div class='stat-txt'>{label}</div></div>"
        f"<div class='stat-num'>{value}</div></div>"
    )


def _stats_row(stats: dict[str, Any]) -> str:
    return (
        "<div class='stats-row'>"
        + _stat_box("msgs", "消息总数", int(stats.get("total") or 0))
        + _stat_box("users", "活跃成员", int(stats.get("participants") or 0))
        + _stat_box("smile", "表情互动", int(stats.get("emojis") or 0))
        + _stat_box("type", "字数累计", int(stats.get("characters") or 0))
        + "</div>"
    )


def _peak_block(stats: dict[str, Any]) -> str:
    peak = stats.get("peak_hour")
    text = f"{peak:02d}:00-{peak + 1:02d}:00" if isinstance(peak, int) else "—"
    return (
        "<div class='active-peak-block'>"
        "<div>"
        f"<div class='peak-label'>{_icon('trending', 'width: 20px; height: 20px;')}峰值活跃时间段</div>"
        f"<div class='peak-time-text'>{text}</div>"
        "</div>"
        "<div class='peak-image-container'><div class='miku-peak-activity-image'></div></div>"
        "</div>"
    )


def _hour_chart(hours: list[int]) -> str:
    peak = max(hours) if hours else 0
    bars = []
    for hour, count in enumerate(hours):
        height = max(round(count / peak * 100), 4) if peak else 4
        color = _CHART_COLORS[hour % len(_CHART_COLORS)] if count else "var(--miku-blue)"
        label = f"<span class='miku-bar-count' style='color:{color};'>{count}</span>" if count else ""
        bars.append(
            f"<div class='miku-bar-col'>{label}"
            f"<div class='miku-bar' style='height:{height}%;background:{color};"
            f"opacity:{'1' if count else '0.3'};'></div></div>"
        )
    axis = []
    for hour in range(len(hours)):
        unit = "<span class='miku-hour-unit'>时</span>" if hour % 4 == 0 or hour == len(hours) - 1 else ""
        axis.append(f"<div class='miku-axis-label'><span>{hour:02d}{unit}</span></div>")
    bars_html = "".join(bars)
    axis_html = "".join(axis)
    return (
        f"<div class='miku-chart'><div class='miku-bars'>{bars_html}</div>"
        f"<div class='miku-axis'>{axis_html}</div></div>"
    )


def _speakers(stats: dict[str, Any]) -> str:
    items = "".join(
        f"<span class='miku-speaker'>{_escape(item.get('name'))} <b>{item.get('count')}</b></span>"
        for item in (stats.get("top_speakers") or [])[:5]
    )
    return f"<div class='miku-speakers'>{items}</div>"


def _overview(stats: dict[str, Any]) -> str:
    return (
        "<div class='card-common' style='padding:20px 10px;'>"
        + _hour_chart(stats.get("hours") or [])
        + _speakers(stats)
        + "</div>"
    )


def _section(title: str, icon: str, body: str, *, accent: bool = False) -> str:
    bar = " accent" if accent else ""
    return (
        "<div class='miku-section'>"
        f"<div class='section-header'><div class='v-bar{bar}'></div>"
        f"<div class='section-title-text'>{_icon(icon)}{title}</div></div>"
        f"{body}</div>"
    )


def _topic_cards(topics: list[dict[str, Any]]) -> str:
    cards = []
    for index, topic in enumerate(topics):
        people = "、".join(topic.get("participants") or [])
        keywords = "".join(f"<span class='miku-tag'>{_escape(word)}</span>" for word in topic.get("keywords") or [])
        tags = f"<div class='miku-topic-tags'>{keywords}</div>" if keywords else ""
        image = _image(_TOPIC_IMAGES[index % len(_TOPIC_IMAGES)])
        cards.append(
            "<div class='card-common miku-topic-card'>"
            f"<div class='miku-topic-index'>#{index + 1:02d}</div>"
            "<div class='miku-topic-body'>"
            f"<div class='miku-topic-title'>{_escape(topic.get('title'))}</div>"
            f"<div class='miku-topic-detail'>{_escape(topic.get('summary'))}</div>"
            f"{tags}<div class='miku-topic-foot'>"
            "<div class='miku-contributors'>"
            f"{_icon('member', 'width: 16px; height: 16px; color: var(--miku-dark);')}"
            f"<span>参与成员:</span><b>{_escape(people) or '—'}</b></div>"
            f"<div class='miku-topic-deco'><img src='{image}' alt='装饰图'/></div>"
            "</div></div></div>"
        )
    return "".join(cards)


def _title_cards(titles: list[dict[str, Any]]) -> str:
    cards = []
    for item in titles:
        name = str(item.get("name") or "")
        cards.append(
            "<div class='card-common miku-user-title-card'>"
            "<div class='miku-user-title-head'>"
            f"<div class='miku-avatar'><div class='miku-avatar-fallback'>{_escape(name[:1] or '?')}</div></div>"
            "<div class='miku-user-title-name-wrap'>"
            f"<div class='miku-user-title-name'>{_escape(name)}</div>"
            "<div class='miku-user-title-tags'>"
            f"<span class='miku-user-title-tag'>{_escape(item.get('title'))}</span></div>"
            "</div></div>"
            f"<div class='miku-user-title-reason'>{_escape(item.get('reason'))}{_QUOTE_WATERMARK}</div>"
            "</div>"
        )
    return f"<div class='miku-user-title-grid'>{''.join(cards)}</div>"


def _quotes(quotes: list[dict[str, Any]]) -> str:
    rows = []
    for index, quote in enumerate(quotes):
        odd = " odd" if index % 2 else ""
        sender = str(quote.get("sender") or "")
        review = _escape(quote.get("reason")) or "这句话值得被记住。"
        rows.append(
            f"<div class='miku-quote-row{odd}'><div class='miku-quote-wrap{odd}'>"
            f"<div class='miku-quote-avatar'><div class='miku-avatar-fallback'>"
            f"{_escape(sender[:1] or '?')}</div></div>"
            f"<div class='miku-quote-body{odd}'>"
            f"<div class='miku-quote-sender'>{_escape(sender)}</div>"
            f"<div class='miku-quote-bubble{odd}'><div>{_escape(quote.get('content'))}</div></div>"
            "<div class='miku-quote-review'><div class='miku-quote-review-head'>"
            f"{_icon('music', 'width: 18px; height: 18px; color: var(--miku-dark);')}"
            "<span>Miku 锐评</span></div>"
            f"<div class='miku-quote-review-text'>{review}</div>"
            "</div></div></div></div>"
        )
    return "".join(rows)


def _has_quality(quality: dict[str, Any]) -> bool:
    return bool(quality.get("dimensions") or quality.get("summary"))


def _quality(quality: dict[str, Any], label: str) -> str:
    dimensions = quality.get("dimensions") or []
    bar = "".join(
        f"<div style='width:{float(item.get('percentage') or 0)}%;"
        f"background:{_DIM_COLORS[index % len(_DIM_COLORS)]};'></div>"
        for index, item in enumerate(dimensions)
    )
    grid = "".join(
        "<div class='miku-quality-item'>"
        f"<div class='miku-dot' style='background:{_DIM_COLORS[index % len(_DIM_COLORS)]};'></div>"
        "<div>"
        f"<div class='miku-quality-name'>{_escape(item.get('name'))} "
        f"({float(item.get('percentage') or 0):.1f}%)</div>"
        f"<div class='miku-quality-comment'>{_escape(item.get('comment'))}</div>"
        "</div></div>"
        for index, item in enumerate(dimensions)
    )
    title = quality.get("title") or "群聊锐评"
    subtitle = quality.get("subtitle") or label or "今天群里发生了什么？"
    summary = quality.get("summary") or "今天群里暂时没什么好锐评的。"
    deco = _image(_TOPIC_IMAGES[0])
    return (
        "<div class='card-common'>"
        "<div class='miku-quality-head'>"
        f"<div class='miku-quality-title'>{_escape(title)}</div>"
        f"<div class='miku-quality-subtitle'>{_escape(subtitle)}</div></div>"
        f"<div class='miku-quality-bar'>{bar}</div>"
        f"<div class='miku-quality-grid'>{grid}</div>"
        "<div class='miku-quality-summary'><div class='miku-quality-summary-body'>"
        "<div class='miku-quality-summary-head'>"
        f"{_icon('music', 'width: 20px; height: 20px; color: white;')}"
        "<span>Miku 综合点评</span></div>"
        f"<div class='miku-quality-summary-text'>{_escape(summary)}</div></div>"
        f"<div class='miku-quality-deco'><img src='{deco}' alt='Quality Deco'/></div>"
        "</div></div>"
    )


def _build_html(report: dict[str, Any]) -> str:
    stats = report.get("stats") or {}
    analysis = report.get("analysis") or {}
    blocks = [_section("群聊概览", "chart", _overview(stats))]
    if topics := analysis.get("topics") or []:
        blocks.append(_section("热门话题", "msgs", _topic_cards(topics), accent=True))
    if titles := analysis.get("titles") or []:
        blocks.append(_section("群友称号", "users", _title_cards(titles)))
    if quotes := analysis.get("quotes") or []:
        blocks.append(_section("当日金句", "quote", _quotes(quotes), accent=True))
    if _has_quality(analysis.get("quality") or {}):
        quality = analysis.get("quality") or {}
        blocks.append(_section("聊天质量", "award", _quality(quality, str(report.get("label") or ""))))
    if len(blocks) == 1:
        blocks.append("<div class='card-common miku-empty'>本次 AI 分析未返回内容，仅展示群聊统计</div>")
    title = _escape(report.get("group_name") or report.get("group_id"))
    now = datetime.now(SHANGHAI_TZ)
    period = f" · {_escape(report.get('label'))}" if report.get("label") else ""
    footer_icon = _icon("msgs", "width: 18px; height: 18px; color: var(--miku-dark); margin-right: 10px;")
    clock_icon = _icon("clock", "width: 14px; height: 14px; margin-right: 4px; color: var(--text-sub);")
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=940, initial-scale=1.0">
<title>{title} · {now.strftime("%Y年%m月%d日")}</title>
{_style()}
</head>
<body>
<div class="container">
  <div class="deco-01">01</div>
  <div class="deco-39">39</div>
  <div class="deco-sys">[ SYSTEM_VOCALOID_INITIALIZED ]<br>HATSUNE MIKU v4X</div>
  <div class="header">
    <h1>{title}</h1>
    <div class="date-line">分析日志 // {now.strftime("%Y年%m月%d日")}{period}</div>
  </div>
  {_stats_row(stats)}
  {_peak_block(stats)}
  {"".join(blocks)}
  <div class="footer">
    <div class="footer-top">
      <div class="footer-repo">{footer_icon}FxBot 群聊日报</div>
      <div class="footer-author">Template by shiitin · HatsuneMiku</div>
    </div>
    <div class="footer-bottom">
      <div class="footer-time">{clock_icon}生成于 {now.strftime("%Y-%m-%d %H:%M:%S")}</div>
      <div class="footer-time">由 AI 生成，仅供娱乐</div>
    </div>
  </div>
</div>
</body>
</html>"""


def _fallback_image(report: dict[str, Any]) -> bytes:
    stats = report.get("stats") or {}
    analysis = report.get("analysis") or {}
    title_font = load_font(get_shared_font_path(), 42)
    text_font = load_font(get_shared_font_path(), 24)
    image = Image.new("RGB", (960, 640), (240, 250, 250))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 960, 190), fill=(57, 197, 187))
    draw.text((48, 46), "群聊日报", fill=(255, 255, 255), font=text_font)
    draw.text((48, 88), str(report.get("group_name") or report.get("group_id")), fill=(255, 255, 255), font=title_font)
    draw.text((48, 146), str(report.get("label") or ""), fill=(224, 242, 241), font=text_font)
    summary = f"消息 {stats.get('total', 0)} · 参与 {stats.get('participants', 0)} · 图片 {stats.get('images', 0)}"
    draw.text((48, 224), summary, fill=(44, 62, 80), font=text_font)
    y = 280
    for topic in (analysis.get("topics") or [])[:3]:
        draw.text((48, y), f"- {topic.get('title', '')}", fill=(44, 62, 80), font=text_font)
        y += 40
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


async def render_report(report: dict[str, Any], scale: float = 2.0) -> bytes:
    """优先用 Playwright 出长图，失败时退化为统计摘要图。"""
    html = _build_html(report)
    try:
        from playwright.async_api import async_playwright
    except Exception:
        return _fallback_image(report)
    try:
        async with _RENDER_SEM:
            pw = await async_playwright().start()
            try:
                browser = await pw.chromium.launch()
                try:
                    page = await browser.new_page(device_scale_factor=scale)
                    try:
                        await asyncio.wait_for(page.set_content(html, wait_until="load"), timeout=20.0)
                        element = await page.query_selector(".container")
                        if element:
                            return await asyncio.wait_for(element.screenshot(type="png"), timeout=20.0)
                        return await asyncio.wait_for(page.screenshot(type="png", full_page=True), timeout=20.0)
                    finally:
                        await page.close()
                finally:
                    await browser.close()
            finally:
                await pw.stop()
    except Exception as exc:
        logger.warning(f"[group_analysis] 长图渲染失败，回退到摘要图: {exc}")
        return _fallback_image(report)
