"""NoneBot 事件预处理会员门禁，必须最早导入。"""

from __future__ import annotations

import re
from collections import deque
from datetime import datetime, timezone
from typing import Any

from nonebot.adapters import Bot, Event
from nonebot.exception import IgnoredException
from nonebot.message import event_preprocessor

from ..config import get_manager as get_config_manager
from ..message_policy import should_process_fxbot_message

from .guard import membership_guard

_RENEW_COMMAND_RE = re.compile(r"^(?:ww到期|ww(?:拉群|续费)|ww续费\d+(?:天|月|年)-[A-Za-z0-9_]+)$")
_PROMPTED_EVENT_IDS: set[int] = set()
_PROMPTED_EVENT_ORDER: deque[int] = deque(maxlen=1024)


def _normalize_id(value: Any) -> str | None:
    """标准化 ID。"""
    if value is None:
        return None
    text = str(value).strip()
    return text if text and text != "0" else None


def _uid(event: Any) -> str | None:
    """提取用户 ID。"""
    if hasattr(event, "get_user_id"):
        try:
            return _normalize_id(event.get_user_id())
        except Exception:
            pass
    return _normalize_id(getattr(event, "user_id", None))


def _gid(event: Any) -> str | None:
    """提取群 ID。"""
    if hasattr(event, "get_group_id"):
        try:
            return _normalize_id(event.get_group_id())
        except Exception:
            pass
    return _normalize_id(getattr(event, "group_id", None))


def _plain_text(event: Any) -> str:
    """提取事件纯文本。"""
    if hasattr(event, "get_plaintext"):
        try:
            return str(event.get_plaintext()).strip()
        except Exception:
            pass
    try:
        return str(event.get_message()).strip()
    except Exception:
        return ""


def _membership_enabled() -> bool:
    """读取会员门禁开关。"""
    cfg = get_config_manager().get_system()
    membership_cfg = cfg["membership"]
    return bool(membership_cfg["enabled"])


def _free_bot_ids() -> set[str]:
    """读取免会员门禁 Bot 列表。"""
    cfg = get_config_manager().get_system()
    membership_cfg = cfg["membership"]
    value = membership_cfg["free_bot_ids"]
    if isinstance(value, (list, tuple, set)):
        return {str(item).strip() for item in value if item is not None and str(item).strip()}
    return set()


def _expire_prompt_text_prefixes() -> tuple[str, ...]:
    """读取触发快到期提示的普通文本前缀。"""
    cfg = get_config_manager().get_system()
    value = cfg["membership"]["expire_prompt_text_prefixes"]
    if isinstance(value, (list, tuple, set)):
        return tuple(str(item).strip() for item in value if item is not None and str(item).strip())
    return ()


def _matches_prompt_text_prefix(text: str) -> bool:
    """判断文本是否匹配快到期提示前缀。"""
    prefixes = _expire_prompt_text_prefixes()
    return bool(prefixes and text.startswith(prefixes))


def _as_utc(value: datetime | None) -> datetime | None:
    """将 datetime 统一转换为 UTC aware 对象。"""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _days_remaining(value: datetime) -> int:
    """按自然日计算剩余天数。"""
    expires_at = _as_utc(value)
    if expires_at is None:
        return 999999
    return (expires_at.date() - datetime.now(timezone.utc).date()).days


def _expire_prompt_threshold() -> int | None:
    """读取快到期命令提示阈值。"""
    cfg = get_config_manager().get_system()
    value = cfg["membership"]["expire_notice_days"]
    try:
        days = int(value)
    except (TypeError, ValueError):
        return None
    return days if days >= 0 else None


def _expiring_prompt(days: int, expires_at: datetime) -> str:
    """生成快到期提示。"""
    cfg = get_config_manager().get_system()
    contact = str(cfg["membership"]["contact_info"] or "").strip()
    expires_text = _as_utc(expires_at).astimezone().strftime("%Y-%m-%d %H:%M:%S")  # type: ignore[union-attr]
    if days <= 0:
        message = f"本群会员今天到期，到期时间：{expires_text}，请及时续费。"
    else:
        message = f"本群会员将在 {days} 天后到期，到期时间：{expires_text}，请及时续费。"
    if contact:
        message += f"\n{contact}"
    return message


def _event_prompted(event: Event) -> bool:
    """判断当前事件是否已发送过快到期提示。"""
    return id(event) in _PROMPTED_EVENT_IDS


def _mark_event_prompted(event: Event) -> None:
    """记录当前事件已发送快到期提示。"""
    event_id = id(event)
    if len(_PROMPTED_EVENT_ORDER) == _PROMPTED_EVENT_ORDER.maxlen:
        old_id = _PROMPTED_EVENT_ORDER.popleft()
        _PROMPTED_EVENT_IDS.discard(old_id)
    _PROMPTED_EVENT_IDS.add(event_id)
    _PROMPTED_EVENT_ORDER.append(event_id)


async def _maybe_send_expiring_prompt(
    bot: Bot,
    event: Event,
    text: str,
    expires_at: datetime | None,
    *,
    require_text_prefix: bool,
) -> None:
    """匹配配置前缀的消息触发时发送快到期提示。"""
    if not expires_at:
        return
    if require_text_prefix and not _matches_prompt_text_prefix(text):
        return
    if _event_prompted(event):
        return
    threshold = _expire_prompt_threshold()
    if threshold is None:
        return
    days = _days_remaining(expires_at)
    if days < 0 or days > threshold:
        return
    try:
        await bot.send(event, _expiring_prompt(days, expires_at))
        _mark_event_prompted(event)
    except Exception:
        pass


@event_preprocessor
async def _fxbot_membership_gate(bot: Bot, event: Event) -> None:
    """群消息会员门禁。"""
    if not should_process_fxbot_message(bot, event):
        return
    if not _membership_enabled():
        return

    bot_id = _normalize_id(getattr(bot, "self_id", None))
    if bot_id and bot_id in _free_bot_ids():
        return

    group_id = _gid(event)
    if not group_id:
        return

    text = _plain_text(event)
    user_id = _uid(event) or ""
    is_renew_command = _RENEW_COMMAND_RE.fullmatch(text) is not None

    try:
        decision = await membership_guard.check_membership_detail(
            group_id,
            user_id,
            bot_id=bot_id,
        )
    except Exception as exc:
        if is_renew_command:
            return
        raise IgnoredException("membership_gate_error") from exc

    if is_renew_command:
        return

    if not decision.allowed:
        raise IgnoredException(f"membership_gate:{decision.reason}")

    await _maybe_send_expiring_prompt(
        bot,
        event,
        text,
        decision.expires_at,
        require_text_prefix=True,
    )
