"""A_Memorix 聊天记录桥接。

A_Memorix 的摘要导入按 ``chat_id`` 读取历史消息并拼成可读文本。FxBot 的
``tenant`` 就是 ``chat_id``，消息存在 ``chat_message`` 表（naive UTC）。
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Sequence

from ....utils.paths import database_path
from ....utils.tz import SHANGHAI_TZ

DEFAULT_LIMIT_CAP = 2000


@dataclass
class _UserInfo:
    user_id: str = ""
    user_nickname: str = ""
    user_cardname: str = ""
    platform: str = ""


@dataclass
class _MessageInfo:
    user_info: _UserInfo = field(default_factory=_UserInfo)
    message_id: str = ""


@dataclass
class _RawMessage:
    components: List[Any] = field(default_factory=list)


@dataclass
class _SessionMessage:
    message_id: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(SHANGHAI_TZ))
    processed_plain_text: str = ""
    message_info: _MessageInfo = field(default_factory=_MessageInfo)
    raw_message: _RawMessage = field(default_factory=_RawMessage)


def _connect() -> sqlite3.Connection | None:
    path = Path(database_path())
    if not path.is_file():
        return None
    try:
        return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=2.0)
    except sqlite3.Error:
        return None


def _has_message_table(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='chat_message' LIMIT 1"
    ).fetchone()
    return row is not None


def _has_turn_item_table(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='chat_turn_item' LIMIT 1"
    ).fetchone()
    return row is not None


def _to_utc_naive(value: float) -> datetime:
    return datetime.fromtimestamp(float(value), tz=timezone.utc).replace(tzinfo=None)


def _strip_speaker_prefix(content: str, sender_id: str, sender_name: str) -> str:
    """去掉 FxBot 存进正文的发言人前缀，摘要文本会自己再加一次。"""
    text = str(content or "")
    user_id = str(sender_id or "").strip()
    name = " ".join(str(sender_name or "").split())[:32]
    label = f"{name}({user_id})" if name and name != user_id and user_id else name or f"用户{user_id}"
    if label and text.startswith(f"{label}:"):
        return text[len(label) + 1 :].lstrip()
    return text


def _row_to_message(row: Sequence[Any]) -> _SessionMessage:
    message_id, kind, sender_id, sender_name, content, occurred_at = row
    text = str(content or "")
    if kind == "assistant":
        from .host_config import global_config

        sender_name = str(sender_name or "") or global_config.bot.nickname
    else:
        text = _strip_speaker_prefix(text, str(sender_id or ""), str(sender_name or ""))
    stored = occurred_at
    if isinstance(stored, str):
        try:
            stored = datetime.fromisoformat(stored)
        except ValueError:
            stored = datetime.utcnow()
    if not isinstance(stored, datetime):
        stored = datetime.utcnow()
    local_time = (
        stored.replace(tzinfo=timezone.utc).astimezone(SHANGHAI_TZ)
        if stored.tzinfo is None
        else stored.astimezone(SHANGHAI_TZ)
    )
    return _SessionMessage(
        message_id=str(message_id or ""),
        timestamp=local_time,
        processed_plain_text=text,
        message_info=_MessageInfo(
            user_info=_UserInfo(
                user_id=str(sender_id or ""),
                user_nickname=str(sender_name or ""),
            ),
            message_id=str(message_id or ""),
        ),
    )


def get_messages_by_time_in_chat(
    chat_id: str,
    start_time: float,
    end_time: float,
    limit: int = 0,
    limit_mode: str = "latest",
    **_kwargs: Any,
) -> List[_SessionMessage]:
    """按租户和时间窗读取消息，返回按时间升序排列的列表。"""
    tenant = str(chat_id or "").strip()
    if not tenant:
        raise ValueError("chat_id 不能为空")
    if not isinstance(start_time, (int, float)) or not isinstance(end_time, (int, float)):
        raise ValueError("start_time 和 end_time 必须是数字类型")

    conn = _connect()
    if conn is None:
        return []
    try:
        if not _has_message_table(conn):
            return []
        descending = str(limit_mode or "latest").lower() != "earliest"
        start, end = _to_utc_naive(start_time), _to_utc_naive(end_time)
        sql = (
            "SELECT COALESCE(platform_message_id, CAST(id AS TEXT)) AS message_id, kind, sender_id, "
            "sender_name, content, occurred_at FROM chat_message "
            "WHERE tenant = ? AND deleted = 0 AND occurred_at >= ? AND occurred_at <= ?"
        )
        params: list[Any] = [tenant, start, end]
        if _has_turn_item_table(conn):
            # 机器人回复在 chat_turn_item 里，摘要和启发式召回都要能看到双方内容
            sql += (
                " UNION ALL SELECT COALESCE(i.platform_message_id, 'turn_item:' || i.id), 'assistant', "
                "'', '', i.content, i.created_at FROM chat_turn_item i "
                "WHERE i.tenant = ? AND i.role = 'assistant' AND i.visible = 1 AND i.send_status = 'sent' "
                "AND i.created_at >= ? AND i.created_at <= ?"
            )
            params.extend([tenant, start, end])
        # 同一时间戳下用户消息在前；latest 查询随后整体反转，tie 键要反向
        sql += (
            f" ORDER BY occurred_at {'DESC' if descending else 'ASC'}, "
            f"kind {'ASC' if descending else 'DESC'}"
        )
        effective_limit = int(limit or 0)
        if effective_limit > 0:
            sql += " LIMIT ?"
            params.append(min(effective_limit, DEFAULT_LIMIT_CAP))
        rows = conn.execute(sql, params).fetchall()
        messages = [_row_to_message(row) for row in rows]
        if descending:
            messages.reverse()
        return messages
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def _format_timestamp(timestamp: float, mode: str) -> str:
    local = datetime.fromtimestamp(float(timestamp), tz=SHANGHAI_TZ)
    normalized = str(mode or "").strip().lower()
    if normalized == "normal":
        return local.strftime("%Y-%m-%d %H:%M:%S")
    if normalized == "normal_no_ymd":
        return local.strftime("%H:%M:%S")
    if normalized == "relative":
        diff = time.time() - float(timestamp)
        if diff < 20:
            return "刚刚"
        if diff < 60:
            return f"{int(diff)}秒前"
        if diff < 3600:
            return f"{int(diff / 60)}分钟前"
        if diff < 86400:
            return f"{int(diff / 3600)}小时前"
        if diff < 86400 * 2:
            return f"{int(diff / 86400)}天前"
        return local.strftime("%Y-%m-%d %H:%M:%S") + ":"
    return local.strftime("%H:%M:%S")


def build_readable_messages(
    messages: Sequence[_SessionMessage],
    replace_bot_name: bool = True,
    timestamp_mode: str = "relative",
    read_mark: float = 0.0,
    truncate: bool = False,
    show_actions: bool = False,
    *,
    group_dates: bool = False,
    include_reply_components: bool = True,
) -> str:
    """按 MaiBot 的格式拼装聊天记录。"""
    del show_actions, include_reply_components
    if group_dates and timestamp_mode != "normal":
        raise ValueError("按日期分组时必须使用 normal 固定时间模式")

    from .host_config import global_config

    bot_name = str(getattr(global_config.bot, "nickname", "") or "")
    lines: List[str] = []
    unread_mark_added = False
    current_date = ""
    for message in messages:
        timestamp = message.timestamp.timestamp()
        if read_mark and not unread_mark_added and timestamp > read_mark:
            lines.append("--- 以上消息是你已经看过，请关注以下未读的新消息 ---")
            unread_mark_added = True

        if group_dates:
            message_date = message.timestamp.strftime("%Y-%m-%d")
            if message_date != current_date:
                lines.append(f"日期：{message_date}")
                current_date = message_date

        text = str(message.processed_plain_text or "").strip()
        if replace_bot_name and bot_name:
            text = text.replace(bot_name, "你")
        user_info = message.message_info.user_info
        user_name = user_info.user_cardname or user_info.user_nickname or user_info.user_id
        prefix = f"[{_format_timestamp(timestamp, 'normal_no_ymd' if group_dates else timestamp_mode)}] "
        line = f"{prefix}{user_name}说：{text}"
        if truncate and len(line) > 200:
            line = f"{line[:200]}......（内容太长了）"
        lines.append(line)
    return "\n".join(lines)
