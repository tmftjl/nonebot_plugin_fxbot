"""全局消息过滤。"""

from __future__ import annotations

from typing import Any

from nonebot.adapters import Event
from nonebot.exception import IgnoredException
from nonebot.message import event_preprocessor

from .adapter.message import event_message, mention_targets
from .config import get_manager as get_config_manager


def _ignored_mention_bot_ids() -> set[str]:
    """读取被 @ 时忽略处理的 Bot QQ 列表。"""
    cfg = get_config_manager().get_system()
    value: Any = cfg["message"]["ignored_mention_bot_ids"]
    if not isinstance(value, (list, tuple, set)):
        return set()
    return {str(item).strip() for item in value if item is not None and str(item).strip()}


@event_preprocessor
async def _ignore_configured_bot_mentions(event: Event) -> None:
    """消息 @ 到指定 Bot QQ 时，不再继续处理本条事件。"""
    ignored_ids = _ignored_mention_bot_ids()
    if not ignored_ids:
        return

    targets = set(mention_targets(event_message(event)))
    if targets & ignored_ids:
        raise IgnoredException("ignored_configured_bot_mention")
