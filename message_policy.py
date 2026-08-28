"""FxBot 消息接收策略。"""

from __future__ import annotations

from nonebot.adapters import Bot, Event

from .adapter.support import event_is_group, event_is_tome, is_qq_official
from .config import get_manager as get_config_manager


def should_process_fxbot_message(bot: Bot, event: Event) -> bool:
    """判断 FxBot 是否应处理该消息，不影响其他插件。"""
    cfg = get_config_manager().get_system()
    requires_mention = bool(cfg["message"]["qq_group_requires_mention"])
    if not requires_mention:
        return True
    return not (is_qq_official(bot) and event_is_group(event) and not event_is_tome(event))
