"""消息处理准入策略。"""

from __future__ import annotations

from typing import Any

from nonebot.adapters import Bot, Event
from nonebot.exception import IgnoredException
from nonebot.message import event_preprocessor

from ..adapter import selfBot
from ..adapter.events import event_is_group, event_is_tome
from ..adapter.message_utils import event_message
from ..config import get_manager as get_config_manager


def should_process_fxbot_message(bot: Bot, event: Event) -> bool:
    cfg = get_config_manager().get_system()
    if not bool(cfg["message"]["qq_group_requires_mention"]):
        return True
    return not (event_is_group(event) and not event_is_tome(event))


def _ignored_mention_bot_ids() -> set[str]:
    value: Any = get_config_manager().get_system()["message"]["ignored_mention_bot_ids"]
    if not isinstance(value, (list, tuple, set)):
        return set()
    return {
        str(item).strip() for item in value if item is not None and str(item).strip()
    }


@event_preprocessor
async def _ignore_configured_bot_mentions(bot: Bot, event: Event) -> None:
    if not should_process_fxbot_message(bot, event):
        return
    ignored_ids = _ignored_mention_bot_ids()
    if ignored_ids and set(selfBot.mention_targets(event_message(event))) & ignored_ids:
        raise IgnoredException("ignored_configured_bot_mention")
