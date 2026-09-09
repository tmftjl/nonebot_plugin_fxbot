"""适配器抽象层。"""

from .uninfo import (
    Role,
    User,
    Scene,
    Member,
    Uninfo,
    Session,
    BotModel,
    MuteInfo,
    Interface,
    SceneType,
    UserModel,
    QryItrface,
    SceneModel,
    UniSession,
    SessionModel,
    SupportScope,
    QueryInterface,
    get_session,
    get_interface,
    get_bot_persist_id,
    get_user_persist_id,
    get_scene_persist_id,
    get_session_persist_id,
)
from .core.bot import (
    PlatformBot,
    PlatformError,
    PlatformAdapter,
    UnsupportedCapability,
    selfBot,
    bind_bot,
    platform_bot,
    event_is_tome,
    event_message,
    event_user_id,
    event_group_id,
    event_is_group,
    event_user_name,
    event_is_private,
    event_message_type,
    extract_message_target,
)
from .platforms.qq import QQOfficialMessageAdapter
from .core.registry import register_adapter, get_platform_adapter
from .platforms.onebot11 import OneBotV11MessageAdapter
from .platforms.onebot12 import OneBotV12MessageAdapter

MessageAdapter = PlatformAdapter
register_message_adapter = register_adapter


def get_message_adapter(bot):
    return get_platform_adapter(bot)


def build_message_segment(bot, segment_type, data=None):
    return get_platform_adapter(bot).build_segment(bot, segment_type, data)


def build_message(bot, *segments):
    return get_platform_adapter(bot).build_message(bot, [item for item in segments if item is not None])


def extract_image_sources(message):
    result = []
    for segment in list(message or []):
        if getattr(segment, "type", "") == "image":
            data = getattr(segment, "data", {}) or {}
            source = data.get("url") or data.get("file") or data.get("file_id")
            if isinstance(source, str) and source and not source.startswith("base64://"):
                result.append(source)
    return result


def extract_raw_image_sources(message):
    result = []
    for segment in list(message or []):
        if getattr(segment, "type", "") == "image":
            data = getattr(segment, "data", {}) or {}
            source = data.get("url") or data.get("file") or data.get("file_id")
            if isinstance(source, (str, bytes)) and source:
                result.append(source)
    return result


def extract_reply_message_id(message):
    for segment in list(message or []):
        if getattr(segment, "type", "") == "reply":
            data = getattr(segment, "data", {}) or {}
            return data.get("id") or data.get("message_id")
    return None


async def get_replied_message(bot, message_id):
    return await get_platform_adapter(bot).get_replied_message(bot, message_id)


async def fetch_image_bytes(source):
    if isinstance(source, bytes):
        return source
    value = str(source or "")
    if value.startswith("base64://"):
        import base64

        return base64.b64decode(value[9:])
    if value.startswith(("http://", "https://")):
        from ..utils.http import get_shared_async_client

        response = await (await get_shared_async_client()).get(value, follow_redirects=True)
        response.raise_for_status()
        return response.content
    try:
        from pathlib import Path

        return Path(value).read_bytes()
    except OSError:
        return None


def extract_first_text_match(message, pattern, *, ignored_segment_types=None):
    ignored = ignored_segment_types or {"image", "reply"}
    for segment in list(message or []):
        if getattr(segment, "type", "") in ignored:
            continue
        data = getattr(segment, "data", {}) or {}
        text = str(data.get("text", "") if getattr(segment, "type", "") == "text" else "").strip()
        if text and (match := pattern.match(text)):
            return match
    return None


def move_non_text_segments_to_end(value):
    message = event_message(value) if hasattr(value, "get_message") else value
    segments = list(message or [])
    reordered = [item for item in segments if getattr(item, "type", "") == "text"] + [
        item for item in segments if getattr(item, "type", "") != "text"
    ]
    if reordered == segments:
        return False
    message.clear()
    message.extend(reordered)
    return True


async def send_message_to_target(bot, target, message):
    return await get_platform_adapter(bot).send_message_to_target(bot, target, message)


async def send_text_to_target(bot, target, text):
    return await get_platform_adapter(bot).send_text_to_target(bot, target, text)


async def send_forward_messages(bot, event, messages, *, nickname="FxBot"):
    return await get_platform_adapter(bot).send_forward_messages(bot, event, messages, nickname=nickname)


async def send_forward_texts(bot, event, texts, *, nickname="FxBot"):
    messages = [build_message(bot, build_message_segment(bot, "text", text)) for text in texts]
    return await send_forward_messages(bot, event, messages, nickname=nickname)


async def image_sources_from_event_or_reply(bot, event):
    sources = extract_raw_image_sources(event_message(event))
    if sources:
        return sources
    reply = getattr(event, "reply", None)
    if reply and (sources := extract_raw_image_sources(getattr(reply, "message", None))):
        return sources
    reply_id = extract_reply_message_id(event_message(event))
    if reply_id is None:
        return []
    try:
        return extract_raw_image_sources(await get_replied_message(bot, reply_id))
    except Exception:
        return []


async def send_ark_message(bot, event, ark_data):
    return await get_platform_adapter(bot).send_event(bot, event, {"type": "ark", "data": ark_data})


# Importing adapters is the SPI discovery hook.
register_adapter(OneBotV11MessageAdapter)
register_adapter(OneBotV12MessageAdapter)
register_adapter(QQOfficialMessageAdapter)

__all__ = [
    "MessageAdapter",
    "OneBotV11MessageAdapter",
    "OneBotV12MessageAdapter",
    "QQOfficialMessageAdapter",
    "build_message",
    "build_message_segment",
    "event_message",
    "event_group_id",
    "event_is_group",
    "event_is_private",
    "event_is_tome",
    "event_message_type",
    "event_user_id",
    "event_user_name",
    "extract_first_text_match",
    "extract_image_sources",
    "extract_raw_image_sources",
    "extract_message_target",
    "extract_reply_message_id",
    "fetch_image_bytes",
    "get_message_adapter",
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
    "BotModel",
    "Interface",
    "Member",
    "MuteInfo",
    "QryItrface",
    "QueryInterface",
    "Role",
    "Scene",
    "SceneModel",
    "SceneType",
    "Session",
    "SessionModel",
    "SupportScope",
    "UniSession",
    "Uninfo",
    "User",
    "UserModel",
    "get_bot_persist_id",
    "get_interface",
    "get_scene_persist_id",
    "get_session",
    "get_session_persist_id",
    "get_user_persist_id",
]
