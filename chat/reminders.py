"""提醒创建、确认与到期发送。"""

from __future__ import annotations

import hashlib
from uuid import uuid4
from typing import Any
from datetime import datetime, timedelta

from nonebot import logger, get_bots

from ..config import get_manager as get_config_manager
from .session import default_session_store
from ..adapter import bind_bot, build_message, build_message_segment
from ..utils.tz import as_shanghai, to_naive_utc, as_shanghai_local
from .memory.models import ChatReminder, utc_now_naive
from .memory.repository import chat_repository

_CLAIM_TIMEOUT = timedelta(minutes=5)
_BACKOFF_MINUTES = (1, 2, 5, 10, 30)
_MAX_CONTENT_CHARS = 200


def reminder_config() -> dict[str, Any]:
    """读取提醒配置；缺失或非法时按关闭处理。"""
    try:
        value = get_config_manager().get_system()["chat"].get("reminder") or {}
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def parse_due(value: str) -> datetime:
    """解析 ISO 时间：带 offset 直接转 UTC，naive 视为北京时间墙钟。"""
    try:
        parsed = datetime.fromisoformat(str(value or "").strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("时间格式不正确，请使用 ISO 8601，例如 2026-10-28T09:00:00+08:00") from exc
    if parsed.tzinfo is None:
        parsed = as_shanghai_local(parsed)
    return to_naive_utc(parsed)


def format_due(due_at: datetime) -> str:
    """把存储时间转成北京时间文本。"""
    return as_shanghai(due_at).strftime("%Y-%m-%d %H:%M")


def reminder_prompt(row: ChatReminder) -> str:
    """生成给用户看的提醒确认文本。"""
    due = format_due(row.due_at)
    if row.status == "pending_confirmation":
        return f"将在 {due} 提醒你「{row.content}」，回复 #确认提醒 {row.id} 生效。"
    return f"将在 {due} 提醒你「{row.content}」。"


def _max_pending() -> int:
    value = reminder_config().get("max_pending_per_user", 10)
    return value if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 100 else 10


def _requires_confirmation() -> bool:
    return bool(reminder_config().get("require_confirmation", True))


async def create(
    tenant: str, user_id: str, content: str, due: str
) -> tuple[ChatReminder, bool]:
    """校验并创建提醒；返回 (row, 是否新建)。"""
    if not reminder_config().get("enabled"):
        raise ValueError("提醒功能未启用")
    text = " ".join(str(content or "").split())[:_MAX_CONTENT_CHARS]
    if not text:
        raise ValueError("提醒内容不能为空")
    due_at = parse_due(due)
    if due_at <= utc_now_naive():
        raise ValueError("提醒时间必须晚于当前时间")
    key = hashlib.sha256(f"{tenant}|{user_id}|{due_at.isoformat()}|{text}".encode("utf-8")).hexdigest()[:32]
    existing = await chat_repository.find_reminder(tenant, key)
    if existing is not None:
        return existing, False
    count = await chat_repository.count_active_reminders(tenant, user_id)
    if count >= _max_pending():
        raise ValueError(f"每位用户最多保留 {_max_pending()} 条提醒，请先取消旧的提醒")
    status = "pending_confirmation" if _requires_confirmation() else "pending"
    row = await chat_repository.create_reminder(tenant, user_id, text, due_at, key, status=status)
    return row, True


async def confirm(tenant: str, user_id: str, reminder_id: int) -> bool:
    """确认待生效提醒。"""
    return await chat_repository.confirm_reminder(tenant, user_id, reminder_id)


async def cancel(tenant: str, user_id: str, reminder_id: int) -> bool:
    """取消尚未发送的提醒。"""
    return await chat_repository.cancel_reminder(tenant, user_id, reminder_id)


def _bot_for(tenant: str) -> Any:
    """优先使用租户所属 Bot，其次任意在线 Bot。"""
    bots = {str(bot_id): bot for bot_id, bot in get_bots().items()}
    parts = tenant.split(":")
    self_id = parts[1] if len(parts) > 2 else ""
    return bots.get(self_id) or next(iter(bots.values()), None)


def _receipt_id(receipt: Any) -> str:
    """读取发送回执里的消息 ID。"""
    value = receipt.get("message_id") if isinstance(receipt, dict) else getattr(receipt, "message_id", None)
    return str(value) if value is not None else ""


async def _deliver(row: ChatReminder) -> str:
    """按租户把提醒发到群或私聊；失败直接抛给调用方记录。"""
    bot = _bot_for(row.tenant)
    if bot is None:
        raise RuntimeError("当前没有在线 Bot，无法发送提醒")
    message = build_message(bot, build_message_segment(bot, "text", f"提醒：{row.content}"))
    platform = bind_bot(bot)
    if ":group:" in row.tenant:
        receipt = await platform.send_group_message(row.tenant.rsplit(":group:", 1)[1], message)
    elif ":private:" in row.tenant:
        receipt = await platform.send_private_message(row.user_id, message)
    else:
        raise RuntimeError("无法识别提醒的发送目标")
    return _receipt_id(receipt)


async def _fail(row: ChatReminder, token: str, exc: Exception, now: datetime) -> None:
    """记录发送失败并安排退避重试。"""
    attempt = row.attempts + 1
    delay = _BACKOFF_MINUTES[min(attempt - 1, len(_BACKOFF_MINUTES) - 1)]
    await chat_repository.finish_reminder(
        row.id,
        token,
        error=f"{type(exc).__name__}: {exc}",
        next_attempt_at=now + timedelta(minutes=delay),
    )


async def reminder_tick(*, now: datetime | None = None) -> int:
    """每分钟领取到期提醒并发送；关闭时保留待发数据。"""
    if not reminder_config().get("enabled"):
        return 0
    current = now or utc_now_naive()
    try:
        await chat_repository.recover_stale_claims(current - _CLAIM_TIMEOUT)
        token = uuid4().hex
        rows = await chat_repository.claim_due_reminders(current, token)
    except Exception:
        logger.opt(exception=True).warning("[Chat] 领取提醒失败")
        return 0
    sent = 0
    for row in rows:
        try:
            async with default_session_store.send_lock(row.tenant):
                message_id = await _deliver(row)
        except Exception as exc:
            logger.warning(f"[Chat] 提醒 #{row.id} 发送失败: {type(exc).__name__}: {exc}")
            await _fail(row, token, exc, current)
            continue
        await chat_repository.finish_reminder(row.id, token, sent_message_id=message_id)
        sent += 1
    return sent
