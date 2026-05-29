"""适配器抽象层。"""

from .message import (
    MessageAdapter,
    build_message,
    build_message_segment,
    event_message,
    extract_first_text_match,
    extract_image_sources,
    extract_raw_image_sources,
    extract_message_target,
    extract_reply_message_id,
    get_message_adapter,
    get_onebot_v11_message_segment_class,
    get_replied_message,
    is_onebot_v11,
    is_qq_official,
    register_message_adapter,
    send_forward_messages,
    send_forward_texts,
    send_ark_message,
    send_text_to_target,
)
from .onebot11 import OneBotV11MessageAdapter
from .qq import QQOfficialMessageAdapter

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
    "get_message_adapter",
    "get_onebot_v11_message_segment_class",
    "get_replied_message",
    "is_onebot_v11",
    "is_qq_official",
    "register_message_adapter",
    "send_forward_messages",
    "send_forward_texts",
    "send_ark_message",
    "send_text_to_target",
]
