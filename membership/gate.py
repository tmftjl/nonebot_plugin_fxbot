"""NoneBot 事件预处理会员门禁，必须最早导入。"""

from __future__ import annotations

import re
from typing import Any

from nonebot.adapters import Bot, Event
from nonebot.exception import IgnoredException
from nonebot.message import event_preprocessor

from ..config import get_manager as get_config_manager

from .guard import membership_guard

_RENEW_COMMAND_RE = re.compile(r"^(?:ww到期|ww(?:拉群|续费)|ww续费\d+(?:天|月|年)-[A-Za-z0-9_]+)$")


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


@event_preprocessor
async def _fxbot_membership_gate(bot: Bot, event: Event) -> None:
    """群消息会员门禁。"""
    if not _membership_enabled():
        return

    bot_id = _normalize_id(getattr(bot, "self_id", None))
    if bot_id and bot_id in _free_bot_ids():
        return

    group_id = _gid(event)
    if not group_id:
        return

    text = _plain_text(event)
    if _RENEW_COMMAND_RE.fullmatch(text):
        return

    user_id = _uid(event) or ""

    try:
        allowed, reason = await membership_guard.check_membership(
            group_id,
            user_id,
            bot_id=bot_id,
        )
    except Exception as exc:
        raise IgnoredException("membership_gate_error") from exc

    if not allowed:
        raise IgnoredException(f"membership_gate:{reason}")
