"""亚托莉主题日报，模板与样式取自原版插件 ATRI 主题。"""

from __future__ import annotations

import re
import html as html_lib
import base64
from typing import Any
from pathlib import Path
from datetime import datetime
from functools import lru_cache

from ...utils.tz import SHANGHAI_TZ
from ...utils.fonts import get_shared_font_path

_ASSETS = Path(__file__).with_name("resources") / "atri"
_SECTION = re.compile(r"{% if (\w+_html) %}(.*?){% endif %}", re.S)
_ASSET_REF = re.compile(r"\{\{\s*t2i_atri_font_mirror\s*\}\}/file/([^'\"\s]+)")
_ASSET_NAMES = {
    "1775130588385_1774881257527_bg1.webp": "bg1.webp",
    "1775130613783_1774881310058_atri3.webp": "atri3.webp",
    "1775130626446_1774881312214_atri1.webp": "atri1.webp",
    "1775130623112_1774881319340_atri4.webp": "atri4.webp",
    "1775130600453_1774881268554_可爱.webp": "cute.webp",
    "1775130609119_1774881269400_可爱-1.webp": "cute-1.webp",
    "1775130588642_1774881263748_可爱-3.webp": "cute-3.webp",
    "1775130585446_1774881263342_观察.webp": "observe.webp",
    "1775130581843_1774881262835_疑惑.webp": "doubt.webp",
    "1775130598778_1774881267181_得意.webp": "smug.webp",
    "1775130599184_1774881267385_得意-1.webp": "smug-1.webp",
    "1775130605165_1774881270686_爱心.webp": "heart.webp",
    "1775130591277_1774881264350_睡觉.webp": "sleep.webp",
    "1775130591487_1774881264336_不要.webp": "refuse.webp",
}
_DIM_COLORS = ("#8fc5ff", "#ffb3c6", "#a8e6cf", "#dcd3ff", "#fff3b0", "#80cbc4", "#ffd3b6", "#b3d4ff")


def _escape(value: Any) -> str:
    return html_lib.escape(str(value or ""))


@lru_cache(maxsize=None)
def _image(name: str) -> str:
    return "data:image/webp;base64," + base64.b64encode((_ASSETS / name).read_bytes()).decode()


@lru_cache(maxsize=1)
def _style() -> str:
    css = (_ASSETS / "quality.css").read_text(encoding="utf-8")
    return f"<style>{css}</style>"


@lru_cache(maxsize=1)
def _template() -> str:
    raw = (_ASSETS / "template.html").read_text(encoding="utf-8-sig")
    body = base64.b64encode(get_shared_font_path().read_bytes()).decode()
    face = (
        f"@font-face{{font-family:'LXGW WenKai';src:url(data:font/ttf;base64,{body}) format('truetype');"
        "font-weight:400;}"
        f"@font-face{{font-family:'LXGW WenKai';src:url(data:font/ttf;base64,{body}) format('truetype');"
        "font-weight:600;}"
        f"@font-face{{font-family:'LXGW WenKai Mono';src:url(data:font/ttf;base64,{body}) format('truetype');"
        "font-weight:400;}"
    )
    raw = re.sub(r"{% if t2i_font_mode.*?{% endif %}", face, raw, count=1, flags=re.S)
    raw = raw.replace(
        "{% if t2i_font_source == 'Mainland' %}'Noto Sans SC', 'Noto Sans TC'"
        "{% else %}'Noto Sans TC', 'Noto Sans SC'{% endif %}",
        "'Noto Sans SC', 'Noto Sans TC'",
    )
    raw = raw.replace(
        "{{ 'zh-CN' if t2i_font_source == 'Mainland' else 'zh-Hant' }}",
        "zh-CN",
    )
    return _ASSET_REF.sub(lambda match: _image(_ASSET_NAMES.get(match.group(1), match.group(1))), raw)


def _hour_chart(hours: list[int]) -> str:
    peak = max(hours) if hours else 0
    rows = []
    for hour, count in enumerate(hours):
        ratio = hour / (len(hours) - 1) if len(hours) > 1 else 0
        if ratio < 0.3333:
            local = ratio / 0.3333
            top = (210 + local * 45, 225 + local * 15, 255 - local * 25)
            bottom = (170 + local * 85, 190 - local * 5, 255 - local * 55)
        elif ratio < 0.6666:
            local = (ratio - 0.3333) / 0.3333
            top = (255 - local * 20, 240 + local * 15, 230 + local * 10)
            bottom = (255 - local * 105, 185 + local * 45, 200)
        else:
            local = (ratio - 0.6666) / 0.3334
            top = (235 - local * 25, 255 - local * 30, 240 + local * 15)
            bottom = (150 + local * 20, 230 - local * 40, 200 + local * 55)
        top = tuple(int(round(value)) for value in top)
        bottom = tuple(int(round(value)) for value in bottom)
        tint = tuple(int(round(value * 0.7)) for value in bottom)
        percentage = round(count / peak * 100, 1) if peak else 0
        rows.append(
            "<div style='flex:1;display:flex;flex-direction:column;align-items:center;min-width:0;height:100%;'>"
            "<div style='flex:1;width:100%;display:flex;flex-direction:column;align-items:center;justify-content:end;'>"
            f"<span style='font-size:10px;font-weight:700;color:rgb({tint[0]},{tint[1]},{tint[2]});"
            "margin-bottom:4px;line-height:1;white-space:nowrap;"
            f"text-shadow:0 1px 0 rgba(255,255,255,0.65);'>{count}</span>"
            "<div style='width:calc(100% - 4px);height:"
            f"{percentage}%;border-radius:10px 10px 0 0;min-height:5px;border:none;"
            f"background:linear-gradient(180deg,rgb({top[0]},{top[1]},{top[2]}) 0%,"
            f"rgb({bottom[0]},{bottom[1]},{bottom[2]}) 100%);"
            f"box-shadow:0 6px 14px rgba({bottom[0]},{bottom[1]},{bottom[2]},0.14);'"
            f" title='{hour}时 - {count}条消息'></div></div>"
            f"<span style='font-size:10px;color:#9a8aa3;margin-top:8px;line-height:1;'>{hour}</span></div>"
        )
    wrapper = (
        "<div class='chart-container'><div style='display:flex;align-items:stretch;height:220px;"
        "gap:5px;padding:20px 0 10px;'>"
    )
    return wrapper + "".join(rows) + "</div></div>"


def _topic_cards(topics: list[dict[str, Any]]) -> str:
    emojis = ("observe.webp", "doubt.webp", "cute.webp", "cute-1.webp", "cute-3.webp")
    ranks = (
        "linear-gradient(135deg, #a9d8ff 0%, #8fc5ff 48%, #8aaeff 100%)",
        "linear-gradient(135deg, #ffc8d8 0%, #ffb7cd 50%, #ffb2b2 100%)",
        "linear-gradient(135deg, #ffe0ad 0%, #ffd29f 46%, #ffc3b8 100%)",
        "linear-gradient(135deg, #c7efe7 0%, #bfe7ff 52%, #c8d8ff 100%)",
    )
    rows = []
    for index, topic in enumerate(topics):
        people = "、".join(topic.get("participants") or []) or "—"
        rows.append(
            "<li class='item item-with-emoji'><div class='item-main'><div class='item-title'>"
            "<span style='display:inline-flex;align-items:center;justify-content:center;min-width:38px;height:26px;"
            "padding:0 10px;margin-right:8px;border-radius:999px;font-size:0.78em;font-weight:800;"
            "letter-spacing:0.2px;color:#fff;vertical-align:middle;"
            f"background:{ranks[index % len(ranks)]};border:1px solid rgba(255,255,255,0.82);'>"
            f"#{index + 1:02d}</span>{_escape(topic.get('title'))}</div>"
            "<div class='item-content'>"
            f"<p><strong>参与的群友:</strong> {_escape(people)}</p>"
            "<div style='margin-top:8px;padding:12px 18px;background:linear-gradient(180deg, rgba(245,250,255,0.95),"
            "rgba(236,247,255,0.88));border-radius:16px;border:1.5px solid rgba(255,255,255,0.8);"
            "box-shadow:0 4px 12px rgba(85,126,169,0.03);color:var(--text-main);line-height:1.6;'>"
            f"{_escape(topic.get('summary'))}</div></div></div>"
            f"<img class='item-emoji topic-emoji' src='{_image(emojis[index % len(emojis)])}' alt='话题表情'></li>"
        )
    return "".join(rows)


def _title_cards(titles: list[dict[str, Any]]) -> str:
    emojis = ("smug.webp", "smug-1.webp", "observe.webp", "refuse.webp")
    badges = (
        ("linear-gradient(135deg, #ffcadf 0%, #ffc1cc 45%, #ffd9b8 100%)", "#b85c7b", "rgba(255, 177, 203, 0.28)"),
        ("linear-gradient(135deg, #bfe7ff 0%, #c8dbff 48%, #d8ccff 100%)", "#5d71b8", "rgba(168, 198, 255, 0.26)"),
        ("linear-gradient(135deg, #c7f3df 0%, #d3f1ff 50%, #e1ddff 100%)", "#4e8e88", "rgba(161, 232, 205, 0.24)"),
        ("linear-gradient(135deg, #ffe5b7 0%, #ffd6c7 48%, #ffd7ef 100%)", "#b57752", "rgba(255, 210, 163, 0.28)"),
    )
    rows = []
    for index, item in enumerate(titles):
        bg, color, shadow = badges[index % len(badges)]
        name = _escape(item.get("name"))
        avatar = str(item.get("avatar_url") or "")
        avatar_html = (
            f"<img src='{_escape(avatar)}' alt='头像' style='width:50px;height:50px;border-radius:50%;"
            "object-fit:cover;border:2px solid #667eea;'>"
            if avatar
            else ""
        )
        rows.append(
            "<li class='item item-with-emoji' style='position:relative;overflow:hidden;'>"
            "<div class='item-main' style='position:relative;z-index:1;'>"
            "<div style='display:flex;align-items:center;gap:15px;'>"
            f"{avatar_html}<div style='flex:1;'><div class='item-title'>{name}"
            "<span style='display:inline-flex;align-items:center;margin-left:10px;padding:5px 10px;"
            "border-radius:999px;font-size:0.78em;font-weight:700;letter-spacing:0.2px;vertical-align:middle;"
            f"color:{color};background:{bg};"
            f"border:1px solid rgba(255,255,255,0.8);"
            f"box-shadow:0 6px 14px {shadow}, inset 0 1px 0 rgba(255,255,255,0.55);'>"
            f"{_escape(item.get('title'))}</span></div><div class='item-content'>"
            f"<p><strong>人格标签:</strong> {_escape(item.get('mbti'))}</p>"
            f"<p><strong>称号理由:</strong> {_escape(item.get('reason'))}</p></div></div></div></div>"
            f"<img class='item-emoji title-emoji' src='{_image(emojis[index % len(emojis)])}' alt='称号表情' "
            "style='z-index:2;'></li>"
        )
    return "".join(rows)


def _quote_cards(quotes: list[dict[str, Any]]) -> str:
    emojis = ("heart.webp", "cute.webp", "sleep.webp", "cute-1.webp")
    rows = []
    for index, quote in enumerate(quotes):
        avatar = str(quote.get("avatar_url") or "")
        avatar_html = (
            f"<div class='q-avatar-box'><img src='{_escape(avatar)}' class='q-avatar'></div>" if avatar else ""
        )
        rows.append(
            "<div class='quote-wrapper'><div class='q-flex-container'>"
            f"<div class='q-user-col'>{avatar_html}</div><div class='q-content-col'>"
            f"<div class='q-sender-name'>{_escape(quote.get('sender'))}</div>"
            "<div class='q-bubble-wrap'><div class='q-bubble'>"
            f"<div class='q-content'>{_escape(quote.get('content'))}</div></div>"
            f"<img class='quote-floating-emoji' src='{_image(emojis[index % len(emojis)])}' alt='金句表情'></div>"
            "<div class='q-analysis-note'><span class='note-label'>📝 亚托莉的高性能分析：</span>"
            f"{_escape(quote.get('reason'))}</div></div></div></div>"
        )
    return "".join(rows)


def _quality_card(quality: dict[str, Any], label: str) -> str:
    dimensions = quality.get("dimensions") or []
    if not dimensions and not quality.get("summary"):
        return ""
    bars = "".join(
        "<div class='quality-bar-row'>"
        f"<div class='quality-bar-label'>{_escape(dim.get('name'))}</div>"
        "<div class='quality-bar-track'><div class='quality-bar-fill' style='width:"
        f"{float(dim.get('percentage') or 0)}%;background:linear-gradient(90deg, "
        f"{_DIM_COLORS[index % len(_DIM_COLORS)]}, {_DIM_COLORS[index % len(_DIM_COLORS)]} 65%, white);'>"
        "</div></div>"
        f"<div class='quality-bar-value'>{float(dim.get('percentage') or 0):.0f}%</div></div>"
        for index, dim in enumerate(dimensions)
    )
    comments = "".join(
        f"<div class='quality-comment'><strong>{_escape(dim.get('name'))}</strong>"
        f"<div>{_escape(dim.get('comment'))}</div></div>"
        for dim in dimensions
    )
    summary = _escape(quality.get("summary") or label)
    return (
        _style() + "<div class='quality-card'><div class='quality-head'><div>"
        f"<div class='quality-title'>{_escape(quality.get('title') or '群聊锐评')}</div>"
        f"<div class='quality-subtitle'>{_escape(quality.get('subtitle') or label)}</div></div></div>"
        f"<div class='quality-bars'>{bars}</div><div class='quality-comment-grid'>{comments}</div>"
        f"<div class='quality-summary'>{summary}</div></div>"
    )


def build_atri_html(report: dict[str, Any]) -> str:
    stats = report.get("stats") or {}
    analysis = report.get("analysis") or {}
    usage = analysis.get("usage") or {}
    label = str(report.get("label") or "")
    template = _template()
    slots = {
        "hourly_chart_html": _hour_chart(stats.get("hours") or []),
        "topics_html": _topic_cards(analysis.get("topics") or []),
        "titles_html": _title_cards(analysis.get("titles") or []),
        "quotes_html": _quote_cards(analysis.get("quotes") or []),
        "chat_quality_html": _quality_card(analysis.get("quality") or {}, label),
    }
    template = _SECTION.sub(lambda match: match.group(2) if slots[match.group(1)] else "", template)
    peak = stats.get("peak_hour")
    current = datetime.now(SHANGHAI_TZ)
    date = str(report.get("report_date") or current.strftime("%Y-%m-%d"))
    values = {
        **slots,
        "message_count": f"{int(stats.get('total') or 0):,}",
        "participant_count": int(stats.get("participants") or 0),
        "total_characters": f"{int(stats.get('characters') or 0):,}",
        "emoji_count": int(stats.get("emojis") or 0),
        "total_tokens": f"{int(usage.get('total_tokens') or 0):,}",
        "prompt_tokens": f"{int(usage.get('prompt_tokens') or 0):,}",
        "completion_tokens": f"{int(usage.get('completion_tokens') or 0):,}",
        "most_active_period": f"{peak:02d}点 ~ {peak + 1:02d}点" if isinstance(peak, int) else "—",
        "current_date": date,
        "current_datetime": f"{date} {current.strftime('%H:%M')}",
    }
    for key, value in values.items():
        template = template.replace(f"{{{{ {key} | safe }}}}", str(value))
        template = template.replace(f"{{{{ {key} }}}}", str(value))
    return template
