"""群聊日报的拉取、分析、渲染与持久化编排。"""

from __future__ import annotations

import re
import json
import base64
import asyncio
from typing import Any
from datetime import timedelta
from dataclasses import dataclass

import httpx
from nonebot import logger

from .config import get_config, is_enabled, group_allowed, resolve_persona
from .models import GroupAnalysisReport, utc_now_naive
from .history import HistoryWindow, build_stats, build_user_stats, collect_messages, build_analysis_messages
from .renderer import render_report
from ...adapter import UnsupportedCapability, platform_bot
from .analyzers import analyze
from ...utils.tz import now
from ...utils.paths import data_dir

_RUNNING: set[str] = set()
_AVATARS: dict[str, str] = {}
_MENTION = re.compile(r"\[([A-Za-z0-9_-]{1,128})\]")

REASON_TEXT = {
    "disabled": "群聊日报功能未启用",
    "group_not_allowed": "本群未启用群聊日报",
    "busy": "该群正在生成日报，请稍后再试",
    "unsupported": "当前平台不支持拉取群历史消息，无法生成日报",
    "fetch_failed": "拉取群消息失败，请稍后再试",
    "too_few": "这段时间的消息太少，暂时无法生成日报",
    "failed": "生成日报失败，请稍后再试",
}


@dataclass(slots=True)
class ReportResult:
    ok: bool
    image: bytes = b""
    reason: str = ""


def is_running(group_id: str) -> bool:
    return group_id in _RUNNING


def manual_label(days: int) -> str:
    return "最近 24 小时" if days <= 1 else f"最近 {days} 天"


def daily_period() -> tuple[int, str, str]:
    """返回今天零点（窗口终点）、前一天标签和日报日期。"""
    today_start = now().replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday = today_start - timedelta(days=1)
    date = yesterday.strftime("%Y-%m-%d")
    return int(today_start.timestamp()), f"{date} 全天", date


async def group_name_for(bot: Any, group_id: str) -> str:
    try:
        info = await platform_bot(bot).get_group_info(group_id)
    except UnsupportedCapability:
        return ""
    except Exception:
        return ""
    if isinstance(info, dict):
        return str(info.get("group_name") or info.get("name") or "")
    return ""


def _replace_mentions(text: str, names: dict[str, str]) -> str:
    return _MENTION.sub(lambda match: names.get(match.group(1), match.group(0)), text or "")


async def _avatar_data(url: str) -> str:
    if not url:
        return ""
    if url not in _AVATARS:
        try:
            async with httpx.AsyncClient(timeout=5, follow_redirects=True) as client:
                response = await client.get(url)
                response.raise_for_status()
            mime = response.headers.get("content-type", "image/png").split(";")[0]
            _AVATARS[url] = f"data:{mime};base64,{base64.b64encode(response.content).decode()}"
        except Exception:
            _AVATARS[url] = ""
    return _AVATARS[url]


async def _attach_avatars(bot: Any, analysis: dict[str, Any]) -> None:
    client = platform_bot(bot)
    items = [*analysis["titles"], *analysis["quotes"]]
    user_urls: dict[str, str] = {}
    for item in items:
        user_id = str(item.get("user_id") or "")
        if not user_id:
            continue
        try:
            url = client.user_avatar(user_id)
        except Exception:
            url = None
        if url:
            user_urls[user_id] = str(url)
    if not user_urls:
        return
    urls = list(dict.fromkeys(user_urls.values()))
    fetched = dict(zip(urls, await asyncio.gather(*(_avatar_data(url) for url in urls)), strict=True))
    for item in items:
        url = user_urls.get(str(item.get("user_id") or ""))
        if url and fetched.get(url):
            item["avatar_url"] = fetched[url]


def _resolve_names(analysis: dict[str, Any], messages: list[dict[str, Any]]) -> None:
    names = {str(msg["user_id"]): str(msg.get("sender") or "") for msg in messages if msg.get("user_id")}
    for topic in analysis["topics"]:
        topic["summary"] = _replace_mentions(topic.get("summary") or "", names)
    for quote in analysis["quotes"]:
        quote["reason"] = _replace_mentions(quote.get("reason") or "", names)
    for title in analysis["titles"]:
        title["reason"] = _replace_mentions(title.get("reason") or "", names)


async def generate(
    bot: Any,
    group_id: str,
    *,
    days: int,
    end: int,
    label: str,
    report_date: str,
    group_name: str = "",
    session_id: str = "",
) -> ReportResult:
    """同一群同时只跑一份；失败只返回预定义原因，不向用户泄露异常文本。"""
    group_id = str(group_id)
    if not is_enabled():
        return ReportResult(False, reason="disabled")
    if not group_allowed(group_id):
        return ReportResult(False, reason="group_not_allowed")
    if group_id in _RUNNING:
        return ReportResult(False, reason="busy")
    _RUNNING.add(group_id)
    try:
        config = get_config()
        basic = config["basic"]
        window = HistoryWindow(start=end - days * 86400, end=end, label=label, days=days)
        try:
            messages = await collect_messages(
                bot,
                group_id,
                window,
                limit=max(1, int(basic.get("max_messages") or 1000)),
                filter_self=bool(basic.get("filter_self", True)),
            )
        except UnsupportedCapability:
            return ReportResult(False, reason="unsupported")
        except Exception:
            logger.opt(exception=True).warning(f"[group_analysis] 群 {group_id} 拉取历史消息失败")
            return ReportResult(False, reason="fetch_failed")
        if len(messages) < int(basic.get("min_messages") or 0):
            return ReportResult(False, reason="too_few")
        features = config["analysis_features"]
        stats = build_stats(messages)
        analysis_messages = build_analysis_messages(messages, {str(getattr(bot, "self_id", "") or "")})
        users = build_user_stats(messages, int(features.get("max_user_titles") or 8))
        analysis = await analyze(
            group_id,
            analysis_messages,
            users=users,
            features=features,
            persona=resolve_persona(session_id),
        )
        _resolve_names(analysis, analysis_messages)
        await _attach_avatars(bot, analysis)
        report = {
            "group_id": group_id,
            "group_name": group_name,
            "label": label,
            "report_date": report_date,
            "stats": stats,
            "analysis": analysis,
        }
        image = await render_report(report, theme=str(basic.get("theme") or "miku"))
        path = data_dir("group_analysis") / "images" / f"{group_id}_{report_date}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(image)
        await GroupAnalysisReport.upsert(
            group_id=group_id,
            report_date=report_date,
            period_start=window.start,
            period_end=window.end,
            message_count=stats["total"],
            participant_count=stats["participants"],
            summary=json.dumps(analysis, ensure_ascii=False, default=str),
            image_path=str(path),
            created_at=utc_now_naive(),
        )
        logger.info(f"[group_analysis] 群 {group_id} 日报生成完成：{stats['total']} 条消息")
        return ReportResult(True, image=image)
    except Exception:
        logger.opt(exception=True).warning(f"[group_analysis] 群 {group_id} 日报生成失败")
        return ReportResult(False, reason="failed")
    finally:
        _RUNNING.discard(group_id)
