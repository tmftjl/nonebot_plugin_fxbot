"""AI 兜底路由，必须在加载内置子插件前导入。"""

from __future__ import annotations

from typing import Any

from nonebot import logger, on_message
from nonebot.adapters import Bot, Event
from nonebot.matcher import Matcher
from nonebot.rule import Rule

from ..adapter.events import event_group_id, event_is_tome
from ..config import get_manager as get_config_manager
from ..message_policy import should_process_fxbot_message
from .message_adapter import adapt_message_event
from .service import chat_service
from .tool_runtime import default_runtime_factory


def _plain_text(event: Any) -> str:
    """提取纯文本。"""
    if hasattr(event, "get_plaintext"):
        try:
            return str(event.get_plaintext()).strip()
        except Exception:
            pass
    try:
        return str(event.get_message()).strip()
    except Exception:
        return ""


def _gid(event: Any) -> str | None:
    """提取群 ID。"""
    return event_group_id(event)


def _is_to_me(event: Any) -> bool:
    """判断消息是否 @bot。"""
    return event_is_tome(event)


def _chat_cfg() -> dict[str, Any]:
    """读取 AI 配置。"""
    cfg = get_config_manager().get_system()
    return cfg["chat"]


async def _ai_fallback_rule(bot: Bot, event: Event) -> bool:
    """AI 兜底匹配规则。"""
    if not should_process_fxbot_message(bot, event):
        return False
    cfg = _chat_cfg()
    if not bool(cfg["enabled"]):
        return False
    text = _plain_text(event)
    if not text:
        return False
    prefixes = tuple(str(item) for item in cfg["command_prefixes"])
    if text.startswith(prefixes):
        return False
    if _gid(event) and bool(cfg["group_requires_mention"]) and not _is_to_me(event):
        return False
    return True


ai_router = on_message(rule=Rule(_ai_fallback_rule), priority=99, block=True)


@ai_router.handle()
async def _handle_ai_fallback(bot: Bot, event: Event, matcher: Matcher) -> None:
    """处理 AI 兜底消息。"""
    request = adapt_message_event(event)
    runtime = default_runtime_factory.create(bot=bot, event=event, matcher=matcher)
    try:
        response = await chat_service.process(request, runtime)
    except Exception as exc:
        logger.opt(exception=True).warning(f"[Chat] AI 兜底处理失败: {exc}")
        await matcher.finish("AI 服务暂时不可用")
    if response.text:
        await matcher.finish(response.text)
