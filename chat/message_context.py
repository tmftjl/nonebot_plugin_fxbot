"""按 MaiBot 062d45c 保留真实聊天消息身份、时间与恢复提醒。"""

from __future__ import annotations

from html import escape
from typing import Any
from datetime import datetime

from .types import speaker_label
from ..utils.tz import now, as_shanghai, to_naive_utc


def message_metadata(
    *, timestamp: datetime, sender_id: str = "", sender_name: str = "", quote_id: str = "", at_bot: bool = False
) -> dict[str, Any]:
    """构造可持久化在原消息 payload 的身份元信息。"""
    return {
        "_timestamp": to_naive_utc(timestamp).isoformat(),
        "_sender_id": sender_id,
        "_sender_name": sender_name,
        "_quote_ids": [quote_id] if quote_id else [],
        "_at_bot": at_bot,
    }


def render_message(message: dict[str, Any], *, bot_name: str = "") -> dict[str, Any]:
    """真实收发消息均作为带前缀的 user 内容，内部工具协议原样保留。"""
    public = {key: value for key, value in message.items() if not key.startswith("_")}
    if message.get("_planner_internal") or message.get("_reference"):
        return public
    timestamp = message.get("_timestamp")
    if not timestamp:
        return public
    attrs = []
    ids = message.get("_source_ids", [])
    if ids:
        attrs.append(f'msg_id="{escape(str(ids[0]), quote=True)}"')
    quotes = message.get("_quote_ids", [])
    if quotes:
        attrs.append(f'quote="{escape(",".join(quotes), quote=True)}"')
    time = as_shanghai(datetime.fromisoformat(timestamp))
    attrs.append(f'time="{time:%H:%M:%S}"')
    name = message.get("_sender_name") or message.get("_sender_id") or bot_name
    attrs.append(f'user="{escape(str(name), quote=True)}"')
    if message.get("_self_message"):
        attrs.append('is_self_message="true"')
    prefix = f"<message {' '.join(attrs)}>\n"
    if message.get("_at_bot"):
        prefix += f"@{bot_name}\n"
    content = public.get("content")
    if isinstance(content, str):
        label = speaker_label(str(message.get("_sender_id") or ""), str(message.get("_sender_name") or ""))
        content = content.removeprefix(f"{label}: ").removeprefix(f"{label}:\n")
    public["role"] = "user"
    public["content"] = (
        prefix + content if isinstance(content, str) else [{"type": "text", "text": prefix}, *(content or [])]
    )
    return public


def restore_reference(history: list[dict[str, Any]]) -> dict[str, Any] | None:
    """移植 MaiBot runtime.py 的上下文恢复提示及离线分档。"""
    if not history or not history[-1].get("_timestamp"):
        return None
    seconds = max(0, int((now() - as_shanghai(datetime.fromisoformat(history[-1]["_timestamp"]))).total_seconds()))
    minutes, remainder = divmod(seconds, 60)
    hours, minute = divmod(minutes, 60)
    days, hour = divmod(hours, 24)
    if seconds < 60:
        elapsed = "不到 1 分钟"
    elif seconds < 3600:
        elapsed = f"{minutes} 分钟" if not remainder else f"{minutes} 分 {remainder} 秒"
    elif seconds < 86400:
        elapsed = f"{hours} 小时" if not minute else f"{hours} 小时 {minute} 分钟"
    else:
        elapsed = f"{days} 天" if not hour else f"{days} 天 {hour} 小时"
    if seconds < 300:
        wakeup = "你只是短暂重启了一下，几乎没有明显中断，可以自然接上刚才的话题。"
    elif seconds < 1800:
        wakeup = f"你短暂离线了 {elapsed}，仍然清楚记得刚才的聊天内容。"
    elif seconds < 21600:
        wakeup = f"你离线休息了 {elapsed}，醒来后仍记得上次关机前的聊天内容。"
    elif seconds < 86400:
        wakeup = f"你沉睡了 {elapsed}，重新上线后仍记得上次关机前的聊天内容。"
    else:
        wakeup = f"你已经离线了 {elapsed}，像沉睡了一段时间；恢复后仍记得上次关机前的聊天内容。"
    return {
        "role": "user",
        "content": (
            "[上下文恢复]\n这是启动时恢复的历史上下文提醒，不代表当前用户刚刚发来新消息。\n"
            f"距离上次关机前最后一条可恢复聊天记录已经过去 {elapsed}。\n{wakeup}\n"
            "前面恢复出来的历史消息是你上次关机前记得的聊天内容；"
            "回复时请结合实际间隔，短间隔自然续聊，长间隔可以表现出刚醒来或重新上线后的状态。"
        ),
        "_reference": True,
    }
