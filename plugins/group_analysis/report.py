"""群聊日报的拉取、分析、渲染与持久化编排。"""

from __future__ import annotations

import json
from typing import Any
from datetime import timedelta
from dataclasses import dataclass

from nonebot import logger

from .config import get_config, is_enabled, group_allowed
from .models import GroupAnalysisReport, utc_now_naive
from .history import HistoryWindow, build_stats, build_transcript, collect_messages
from .analyzer import analyze
from .renderer import render_report
from ...adapter import UnsupportedCapability, platform_bot
from ...utils.tz import now
from ...utils.paths import data_dir

_RUNNING: set[str] = set()

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


async def generate(
    bot: Any,
    group_id: str,
    *,
    days: int,
    end: int,
    label: str,
    report_date: str,
    group_name: str = "",
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
        window = HistoryWindow(start=end - days * 86400, end=end, label=label, days=days)
        try:
            messages = await collect_messages(
                bot,
                group_id,
                window,
                limit=max(1, int(config.get("max_messages") or 1000)),
                filter_self=bool(config.get("filter_self", True)),
            )
        except UnsupportedCapability:
            return ReportResult(False, reason="unsupported")
        except Exception:
            logger.opt(exception=True).warning(f"[group_analysis] 群 {group_id} 拉取历史消息失败")
            return ReportResult(False, reason="fetch_failed")
        if len(messages) < int(config.get("min_messages") or 0):
            return ReportResult(False, reason="too_few")
        stats = build_stats(messages)
        analysis = await analyze(group_id, label, stats, build_transcript(messages))
        report = {
            "group_id": group_id,
            "group_name": group_name,
            "label": label,
            "stats": stats,
            "analysis": analysis,
            "sections": {key: bool(value) for key, value in (config.get("sections") or {}).items()},
        }
        image = await render_report(report)
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
