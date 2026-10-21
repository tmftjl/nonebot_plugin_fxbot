"""AI 兜底路由，必须在加载内置子插件前导入。"""

from __future__ import annotations

from typing import Any

from nonebot import logger, on_message
from nonebot.rule import Rule
from nonebot.matcher import Matcher
from nonebot.adapters import Bot, Event

from ..config import get_manager as get_config_manager
from .service import chat_service
from ..adapter import (
    normalize_id,
    event_is_tome,
    event_message,
    event_user_id,
    event_group_id,
    event_user_name,
    event_plain_text,
    extract_raw_image_sources,
)
from .tool_runtime import default_runtime_factory
from .message_adapter import resolve_images, adapt_message_event, session_id_for_event
from ..permission.message_policy import should_process_fxbot_message


def _is_to_me(event: Any) -> bool:
    """判断消息是否 @bot。"""
    return event_is_tome(event)


def _chat_cfg() -> dict[str, Any]:
    """读取 AI 配置。"""
    cfg = get_config_manager().get_system()
    return cfg["chat"]


def _id_allowlist(cfg: dict[str, Any], key: str) -> set[str]:
    """读取 ID 白名单；空集合表示不限制。"""
    value = cfg[key]
    if not isinstance(value, (list, tuple, set)):
        return set()
    return {str(item).strip() for item in value if item is not None and str(item).strip()}


def _has_image(event: Any) -> bool:
    """判断消息是否携带图片，用于放行纯图片消息。"""
    try:
        return bool(extract_raw_image_sources(event_message(event)))
    except Exception:
        return False


def _allowed_group_id(cfg: dict[str, Any], event: Any) -> str | None:
    """返回允许使用 AI 的群 ID，不在白名单内时返回 None。"""
    group_id = event_group_id(event)
    if not group_id:
        return None
    allowlist = _id_allowlist(cfg, "group_allowlist")
    return group_id if not allowlist or group_id in allowlist else None


def _match_context(cfg: dict[str, Any], event: Any) -> bool:
    """按会话类型判断是否允许进入 AI 兜底。"""
    if event_group_id(event):
        return bool(_allowed_group_id(cfg, event)) and _is_to_me(event)
    if not bool(cfg["private_enabled"]):
        return False
    allowlist = _id_allowlist(cfg, "private_allowlist")
    return not allowlist or event_user_id(event) in allowlist


async def _ai_fallback_rule(bot: Bot, event: Event) -> bool:
    """AI 兜底匹配规则。"""
    if not should_process_fxbot_message(bot, event):
        return False
    cfg = _chat_cfg()
    if not bool(cfg["enabled"]):
        return False
    if not _match_context(cfg, event):
        return False
    text = event_plain_text(event)
    if not text and not _has_image(event):
        return False
    prefixes = tuple(str(item) for item in cfg["command_prefixes"])
    if text and text.startswith(prefixes):
        return False
    return True


async def _context_rule(bot: Bot, event: Event) -> bool:
    """记录未 @ Bot 的群消息，作为后续对话的上下文。"""
    if not should_process_fxbot_message(bot, event):
        return False
    cfg = _chat_cfg()
    if not bool(cfg["enabled"]) or int(cfg["group_history"]) <= 0:
        return False
    if not _allowed_group_id(cfg, event) or _is_to_me(event):
        return False
    text = event_plain_text(event)
    prefix = tuple(str(item) for item in cfg["command_prefixes"])
    if text and text.startswith(prefix):
        return False
    return bool(text) or _has_image(event)


# 优先级高于 AI 兜底：先记录群聊上下文，再由兜底决定是否回复
context_router = on_message(rule=Rule(_context_rule), priority=98, block=False)
ai_router = on_message(rule=Rule(_ai_fallback_rule), priority=99, block=True)


@context_router.handle()
async def _handle_context(bot: Bot, event: Event) -> None:
    """把普通群消息写入 AI 会话历史。"""
    user_id = normalize_id(event_user_id(event)) or ""
    has_image = _has_image(event)
    images = await resolve_images(event) if has_image and bool(_chat_cfg()["image_description"]) else []
    await chat_service.record_group_message(
        session_id_for_event(event),
        user_id,
        event_user_name(event, user_id),
        event_plain_text(event),
        images=images,
        has_image=has_image,
    )


@ai_router.handle()
async def _handle_ai_fallback(bot: Bot, event: Event, matcher: Matcher) -> None:
    """处理 AI 兜底消息。"""
    request = await adapt_message_event(bot, event)
    runtime = default_runtime_factory.create(bot=bot, event=event, matcher=matcher)
    try:
        response = await chat_service.process(request, runtime)
    except Exception as exc:
        logger.opt(exception=True).warning(f"[Chat] AI 兜底处理失败: {exc}")
        await matcher.finish("AI 服务暂时不可用")
    if response.text:
        await matcher.finish(response.text)
