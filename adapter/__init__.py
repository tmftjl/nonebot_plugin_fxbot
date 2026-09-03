"""适配器抽象层。"""

from .bot import PlatformBot, bind_bot, platform_bot, selfBot
from .interfaces import PlatformAdapter, PlatformError, UnsupportedCapability
from .message_utils import (
    MessageAdapter,
    build_message,
    build_message_segment,
    event_message,
    extract_first_text_match,
    extract_image_sources,
    extract_message_target,
    extract_raw_image_sources,
    extract_reply_message_id,
    fetch_image_bytes,
    get_message_adapter,
    get_onebot_v11_message_segment_class,
    get_replied_message,
    image_sources_from_event_or_reply,
    move_non_text_segments_to_end,
    register_message_adapter,
    send_ark_message,
    send_forward_messages,
    send_forward_texts,
    send_message_to_target,
    send_text_to_target,
)
from .onebot11 import OneBotV11MessageAdapter
from .qq import QQOfficialMessageAdapter
from .registry import get_platform_adapter, register_adapter

# Importing adapters is the SPI discovery hook.
register_adapter(OneBotV11MessageAdapter)
register_adapter(QQOfficialMessageAdapter)

__all__ = [
    "MessageAdapter",
    "OneBotV11MessageAdapter",
    "QQOfficialMessageAdapter",
    "build_message",
    "build_message_segment",
    "event_message",
    "extract_first_text_match",
    "extract_image_sources",
    "extract_raw_image_sources",
    "extract_message_target",
    "extract_reply_message_id",
    "fetch_image_bytes",
    "get_message_adapter",
    "get_onebot_v11_message_segment_class",
    "get_replied_message",
    "image_sources_from_event_or_reply",
    "move_non_text_segments_to_end",
    "register_message_adapter",
    "send_forward_messages",
    "send_forward_texts",
    "send_message_to_target",
    "send_ark_message",
    "send_text_to_target",
    "PlatformAdapter",
    "PlatformError",
    "UnsupportedCapability",
    "get_platform_adapter",
    "register_adapter",
    "PlatformBot",
    "platform_bot",
    "bind_bot",
    "selfBot",
]
