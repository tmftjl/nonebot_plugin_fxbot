"""群聊日报长图渲染。"""

from __future__ import annotations

import io
import html as html_lib
import base64
import asyncio
from typing import Any
from functools import lru_cache

from PIL import Image, ImageDraw
from nonebot import logger

from ...utils.fonts import load_font, get_shared_font_path

_RENDER_SEM = asyncio.Semaphore(2)


@lru_cache(maxsize=1)
def _font_face() -> str:
    path = get_shared_font_path()
    encoded = base64.b64encode(path.read_bytes()).decode()
    return f"@font-face{{font-family:'FZB';src:url(data:font/ttf;base64,{encoded}) format('truetype');}}"


def _escape(value: Any) -> str:
    return html_lib.escape(str(value or ""))


def _tag_list(values: list[str]) -> str:
    return "".join(f"<span class='tag'>{_escape(value)}</span>" for value in values)


def _hour_chart(hours: list[int]) -> str:
    peak = max(hours) if hours else 0
    bars = []
    for hour, count in enumerate(hours or []):
        height = max(3, round(count / peak * 56)) if peak else 3
        bars.append(f"<div class='bar' title='{hour} 时 {count} 条'><i style='height:{height}px'></i></div>")
    return "<div class='hours'>" + "".join(bars) + "</div>"


def _topic_cards(topics: list[dict[str, Any]]) -> str:
    if not topics:
        return "<div class='empty'>模型未返回可用话题</div>"
    cards = []
    for index, topic in enumerate(topics, 1):
        people = "、".join(topic.get("participants") or [])
        cards.append(
            "<article class='topic'>"
            f"<div class='topic-head'><span class='index'>{index}</span>"
            f"<h3>{_escape(topic.get('title'))}</h3></div>"
            f"<p>{_escape(topic.get('summary'))}</p>"
            f"<div class='tags'>{_tag_list(topic.get('keywords') or [])}</div>"
            + (f"<div class='people'>参与：{_escape(people)}</div>" if people else "")
            + "</article>"
        )
    return "<div class='topics'>" + "".join(cards) + "</div>"


def _title_cards(titles: list[dict[str, Any]]) -> str:
    if not titles:
        return "<div class='empty'>模型未返回可用称号</div>"
    cards = []
    for item in titles:
        name = _escape(item.get("name"))
        cards.append(
            "<article class='member'>"
            f"<div class='avatar'>{_escape(name[:1])}</div>"
            f"<div><div class='member-name'>{name}</div>"
            f"<div class='member-title'>{_escape(item.get('title'))}</div>"
            f"<div class='member-reason'>{_escape(item.get('reason'))}</div></div>"
            "</article>"
        )
    return "<div class='members'>" + "".join(cards) + "</div>"


def _quotes(quotes: list[dict[str, Any]]) -> str:
    if not quotes:
        return "<div class='empty'>模型未返回可用金句</div>"
    items = []
    for quote in quotes:
        items.append(
            "<figure class='quote'>"
            f"<blockquote>{_escape(quote.get('content'))}</blockquote>"
            f"<figcaption>{_escape(quote.get('sender'))}"
            + (f" · {_escape(quote.get('reason'))}" if quote.get("reason") else "")
            + "</figcaption></figure>"
        )
    return "<div class='quotes'>" + "".join(items) + "</div>"


def _quality(quality: dict[str, Any]) -> str:
    score = int(quality.get("score") or 0)
    highlights = "".join(f"<li>{_escape(item)}</li>" for item in quality.get("highlights") or [])
    suggestions = "".join(f"<li>{_escape(item)}</li>" for item in quality.get("suggestions") or [])
    return (
        "<div class='quality'>"
        f"<div class='score' style='--score:{score}'><span>{score}</span><small>分</small></div>"
        f"<div class='quality-body'><p>{_escape(quality.get('comment')) or '暂无评价'}</p>"
        + (f"<div class='quality-list'><b>亮点</b><ul>{highlights}</ul></div>" if highlights else "")
        + (f"<div class='quality-list'><b>建议</b><ul>{suggestions}</ul></div>" if suggestions else "")
        + "</div></div>"
    )


def _stats_panel(stats: dict[str, Any]) -> str:
    speakers = "".join(
        f"<span class='speaker'>{_escape(item.get('name'))} <b>{item.get('count')}</b></span>"
        for item in (stats.get("top_speakers") or [])[:5]
    )
    peak = stats.get("peak_hour")
    peak_text = f"{peak:02d}:00" if isinstance(peak, int) else "—"
    return (
        "<div class='panel'>"
        f"<div class='panel-head'><b>活跃分布</b><span>高峰时段 {peak_text}</span></div>"
        f"{_hour_chart(stats.get('hours') or [])}"
        f"<div class='speakers'>{speakers}</div></div>"
    )


def _build_html(report: dict[str, Any]) -> str:
    stats = report.get("stats") or {}
    analysis = report.get("analysis") or {}
    blocks = [
        "<section><h2><i></i>群聊概览</h2>" + _stats_panel(stats) + "</section>",
        "<section><h2><i></i>热门话题</h2>" + _topic_cards(analysis.get("topics") or []) + "</section>",
        "<section><h2><i></i>群友称号</h2>" + _title_cards(analysis.get("titles") or []) + "</section>",
        "<section><h2><i></i>当日金句</h2>" + _quotes(analysis.get("quotes") or []) + "</section>",
        "<section><h2><i></i>群聊质量</h2>" + _quality(analysis.get("quality") or {}) + "</section>",
    ]
    title = _escape(report.get("group_name") or report.get("group_id"))
    return f"""<!doctype html>
<html><head><meta charset='utf-8'/>
<style>
{_font_face()}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'FZB','Microsoft YaHei',sans-serif;background:#eef1f7;color:#1f2430;width:960px}}
.report{{width:960px;padding:0 0 36px;background:linear-gradient(180deg,#eef1f7 0%,#f7f8fb 100%)}}
header{{position:relative;padding:44px 48px 38px;color:#fff;overflow:hidden;
background:linear-gradient(135deg,#243b7a 0%,#3d5fc4 55%,#6d8ff0 100%)}}
header:after{{content:'';position:absolute;width:320px;height:320px;border-radius:50%;background:rgba(255,255,255,.08);right:-90px;top:-120px}}
.kicker{{font-size:18px;letter-spacing:8px;opacity:.8}}
h1{{font-size:46px;line-height:1.2;margin:10px 0 6px;font-weight:600}}
.period{{font-size:18px;opacity:.85}}
.stats{{display:flex;gap:14px;margin-top:28px}}
.stat{{flex:1;padding:16px 20px;border-radius:16px;background:rgba(255,255,255,.14);backdrop-filter:blur(4px)}}
.stat b{{display:block;font-size:32px;line-height:1.15}}
.stat span{{font-size:15px;opacity:.82}}
section{{margin:26px 40px 0;padding:26px 28px;border-radius:22px;background:#fff;
box-shadow:0 10px 30px rgba(35,55,110,.07)}}
h2{{display:flex;align-items:center;gap:10px;font-size:24px;font-weight:600;margin-bottom:18px}}
h2 i{{width:8px;height:22px;border-radius:4px;background:linear-gradient(180deg,#4d6fe8,#7d9bff)}}
.panel-head{{display:flex;justify-content:space-between;color:#6b7385;font-size:15px;margin-bottom:10px}}
.hours{{display:flex;align-items:flex-end;gap:5px;height:62px;padding:0 2px;border-bottom:1px solid #e6e9f2}}
.bar{{flex:1;display:flex;align-items:flex-end;justify-content:center}}
.bar i{{display:block;width:100%;border-radius:4px 4px 0 0;background:linear-gradient(180deg,#7d9bff,#4d6fe8)}}
.speakers{{display:flex;flex-wrap:wrap;gap:8px;margin-top:14px}}
.speaker{{padding:6px 12px;border-radius:999px;background:#f0f3fb;color:#46506a;font-size:15px}}
.speaker b{{color:#3552bd;margin-left:4px}}
.topics{{display:flex;flex-direction:column;gap:14px}}
.topic{{padding:18px 20px;border-radius:16px;background:#f7f9fe;border:1px solid #edf0f9}}
.topic-head{{display:flex;align-items:center;gap:10px;margin-bottom:8px}}
.index{{width:26px;height:26px;border-radius:8px;background:#4d6fe8;color:#fff;font-size:15px;display:flex;align-items:center;justify-content:center}}
.topic h3{{font-size:19px;font-weight:600}}
.topic p{{font-size:16px;line-height:1.65;color:#4a5268}}
.tags{{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}}
.tag{{font-size:13px;padding:3px 9px;border-radius:999px;background:#e8edff;color:#3d56b0}}
.people{{margin-top:8px;font-size:14px;color:#7a8299}}
.members{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}
.member{{display:flex;gap:12px;padding:14px;border-radius:16px;background:#f7f9fe;border:1px solid #edf0f9}}
.avatar{{flex:0 0 42px;width:42px;height:42px;border-radius:50%;color:#fff;font-size:20px;
display:flex;align-items:center;justify-content:center;
background:linear-gradient(135deg,#6d8ff0,#4d6fe8)}}
.member-name{{font-size:15px;color:#7a8299}}
.member-title{{font-size:19px;font-weight:600;margin:2px 0 4px}}
.member-reason{{font-size:14px;line-height:1.5;color:#5a6278}}
.quotes{{display:flex;flex-direction:column;gap:12px}}
.quote{{padding:16px 20px;border-left:4px solid #6d8ff0;border-radius:0 14px 14px 0;background:#f7f9fe}}
.quote blockquote{{font-size:17px;line-height:1.65}}
.quote figcaption{{margin-top:8px;font-size:14px;color:#7a8299}}
.quality{{display:flex;gap:24px;align-items:flex-start}}
.score{{flex:0 0 120px;width:120px;height:120px;border-radius:50%;position:relative;
display:flex;flex-direction:column;align-items:center;justify-content:center;
background:conic-gradient(#4d6fe8 calc(var(--score)*1%),#e8ecf6 0)}}
.score:before{{content:'';position:absolute;inset:9px;border-radius:50%;background:#fff}}
.score span,.score small{{position:relative;color:#2f3a56}}
.score span{{font-size:38px;font-weight:600;line-height:1}}
.score small{{font-size:14px;color:#7a8299}}
.quality-body{{flex:1;font-size:16px;line-height:1.65;color:#4a5268}}
.quality-list{{margin-top:12px;font-size:15px}}
.quality-list b{{color:#3552bd}}
.quality-list ul{{margin:6px 0 0 20px;color:#5a6278}}
.empty{{padding:18px;border-radius:14px;background:#f7f9fe;color:#9aa1b4;font-size:15px;text-align:center}}
footer{{margin:26px 40px 0;text-align:center;font-size:13px;color:#9aa1b4}}
</style></head>
<body><div class='report'>
<header>
  <div class='kicker'>群聊日报</div>
  <h1>{title}</h1>
  <div class='period'>{_escape(report.get("label"))}</div>
  <div class='stats'>
    <div class='stat'><b>{int(stats.get("total") or 0)}</b><span>消息总数</span></div>
    <div class='stat'><b>{int(stats.get("participants") or 0)}</b><span>参与人数</span></div>
    <div class='stat'><b>{int(stats.get("images") or 0)}</b><span>图片消息</span></div>
  </div>
</header>
{"".join(blocks)}
<footer>FxBot 群聊日报 · 由 AI 生成，仅供娱乐</footer>
</div></body></html>"""


def _fallback_image(report: dict[str, Any]) -> bytes:
    stats = report.get("stats") or {}
    analysis = report.get("analysis") or {}
    title_font = load_font(get_shared_font_path(), 42)
    text_font = load_font(get_shared_font_path(), 24)
    image = Image.new("RGB", (960, 640), (238, 241, 247))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 960, 190), fill=(61, 95, 196))
    draw.text((48, 46), "群聊日报", fill=(255, 255, 255), font=text_font)
    draw.text((48, 88), str(report.get("group_name") or report.get("group_id")), fill=(255, 255, 255), font=title_font)
    draw.text((48, 146), str(report.get("label") or ""), fill=(220, 228, 255), font=text_font)
    summary = f"消息 {stats.get('total', 0)} · 参与 {stats.get('participants', 0)} · 图片 {stats.get('images', 0)}"
    draw.text((48, 224), summary, fill=(40, 48, 70), font=text_font)
    y = 280
    for topic in (analysis.get("topics") or [])[:3]:
        draw.text((48, y), f"- {topic.get('title', '')}", fill=(40, 48, 70), font=text_font)
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
                        element = await page.query_selector(".report")
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
