"""自动绑定 NoneBot 事件对应的平台 Bot。"""

from nonebot.typing import T_State
from nonebot.message import event_preprocessor
from nonebot.adapters import Bot, Event

from .bot import bind_bot, event_message
from .registry import get_platform_adapter


@event_preprocessor
async def _bind_platform_bot(bot: Bot, event: Event, state: T_State) -> None:
    bind_bot(bot)
    get_platform_adapter(bot).remember_event(bot, event, event_message(event))
