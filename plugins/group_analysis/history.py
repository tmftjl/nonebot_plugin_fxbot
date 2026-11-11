"""群历史消息拉取与本地统计。"""

from __future__ import annotations

from typing import Any
from datetime import datetime
from dataclasses import dataclass

from ...adapter import HistoryMessage, fetch_group_history
from ...utils.tz import SHANGHAI_TZ

MAX_TRANSCRIPT_CHARS = 40000
MAX_MESSAGE_CHARS = 200


@dataclass(slots=True)
class HistoryWindow:
    """分析窗口与展示标签。"""

    start: int
    end: int
    label: str
    days: int


def day_window(days: int, *, end: int, label: str) -> HistoryWindow:
    return HistoryWindow(start=end - days * 86400, end=end, label=label, days=days)


async def collect_messages(
    bot: Any,
    group_id: str,
    window: HistoryWindow,
    *,
    limit: int,
    filter_self: bool,
) -> list[HistoryMessage]:
    """拉取窗口内消息；机器人自己的发言可选排除。"""
    messages = await fetch_group_history(bot, group_id, start_time=window.start, limit=limit)
    self_id = str(getattr(bot, "self_id", "") or "")
    if filter_self and self_id:
        messages = [message for message in messages if message.user_id != self_id]
    return [message for message in messages if window.start <= message.timestamp < window.end]


def build_stats(messages: list[HistoryMessage]) -> dict[str, Any]:
    """本地统计不依赖模型，模型失败时仍能出概览。"""
    speakers: dict[str, dict[str, Any]] = {}
    hours = [0] * 24
    images = 0
    for message in messages:
        images += message.images
        name = message.user_name or message.user_id or "未知成员"
        speaker = speakers.setdefault(message.user_id or name, {"name": name, "count": 0})
        speaker["count"] += 1
        if 0 <= (hour := datetime.fromtimestamp(message.timestamp, SHANGHAI_TZ).hour) < 24:
            hours[hour] += 1
    ranked = sorted(speakers.values(), key=lambda item: item["count"], reverse=True)
    peak_hour = max(range(24), key=lambda hour: hours[hour]) if any(hours) else None
    return {
        "total": len(messages),
        "participants": len(speakers),
        "images": images,
        "top_speakers": ranked[:5],
        "hours": hours,
        "peak_hour": peak_hour,
    }


def build_transcript(messages: list[HistoryMessage]) -> str:
    """按时间正序拼接分析文本，超长时保留最近的发言。"""
    lines: list[str] = []
    for message in messages:
        text = " ".join(message.text.split())
        if len(text) > MAX_MESSAGE_CHARS:
            text = text[:MAX_MESSAGE_CHARS] + "…"
        stamp = datetime.fromtimestamp(message.timestamp, SHANGHAI_TZ).strftime("%H:%M")
        lines.append(f"[{stamp}] {message.user_name or message.user_id}: {text}")
    return "\n".join(lines)[-MAX_TRANSCRIPT_CHARS:]
