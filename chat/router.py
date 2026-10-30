"""AI 兜底路由，必须在加载内置子插件前导入。"""

from __future__ import annotations

import re
from typing import Any

from nonebot import logger, on_message
from nonebot.rule import Rule
from nonebot.matcher import Matcher
from nonebot.adapters import Bot, Event

from . import emoji as emoji_module
from .focus import focus_mode_manager
from .types import ChatRequest, TriggerKind
from .voice import transcribe_event, audio_sources_for_event
from ..config import get_manager as get_config_manager
from .service import chat_service, history_limit
from ..adapter import (
    normalize_id,
    event_is_tome,
    event_message,
    event_user_id,
    event_group_id,
    event_user_name,
    event_plain_text,
    extract_message_target,
    extract_raw_image_sources,
)
from .personas import effective_persona_name
from .tool_runtime import default_runtime_factory
from .behavior.quote import quote_counter
from .message_adapter import (
    message_text,
    mention_flags,
    event_identity,
    collect_mentions,
    adapt_message_event,
    memory_image_sources,
    session_id_for_event,
    image_sources_for_event,
)
from .behavior.debounce import default_debounce_registry as default_debounce_registry
from .behavior.reply_gate import proactive_switch, default_reply_gate_registry
from .focus.group_runtime import FocusMember, FocusMatcher
from .behavior.turn_scheduler import normal_scheduler
from ..permission.message_policy import should_process_fxbot_message


def _is_to_me(event: Any) -> bool:
    """判断消息是否 @bot。"""
    return event_is_tome(event)


def _persona_trigger(cfg: dict[str, Any], bot: Any, event: Any) -> str:
    """消息以当前人格名或别名开头时返回命中的名字，用于免 @ 叫醒 Bot。"""
    if not event_group_id(event):
        return ""
    text = event_plain_text(event).lstrip()
    if not text:
        return ""
    behavior = cfg.get("behavior")
    aliases = behavior.get("persona_aliases") if isinstance(behavior, dict) else None
    names = [effective_persona_name(session_id_for_event(bot, event), cfg.get("persona"))]
    names.extend(str(alias).strip() for alias in aliases or [] if str(alias).strip())
    for name in names:
        if name and text.casefold().startswith(name.casefold()):
            return name
    return ""


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


def _has_voice(event: Any) -> bool:
    """判断消息是否携带语音，用于放行纯语音消息；转写在适配阶段完成。"""
    return bool(audio_sources_for_event(event))


_PROACTIVE_ATTR = "_fxbot_proactive_pass"


async def _proactive_gate(bot: Any, event: Any, *, text: str, has_image: bool, has_voice: bool = False) -> bool:
    """普通群消息只采样一次；群级开关开启时才可能转为主动回复。"""
    identity = event_identity(bot, event)
    gate = default_reply_gate_registry.get(identity.session_id)
    sample = text or ("[图片]" if has_image else "[语音]" if has_voice else "")
    has_at_bot, at_other = mention_flags(bot, event)
    observation = gate.observe_message([identity.dedupe_key], [sample], has_at_bot=has_at_bot, at_other=at_other)
    if await proactive_switch(identity.session_id) is False:
        gate.reset_pending()
        return False
    if not observation.would_trigger or gate.backoff_delay() > 0:
        return False
    gate.mark_triggered()
    return True


def _allowed_group_id(cfg: dict[str, Any], event: Any) -> str | None:
    """返回允许使用 AI 的群 ID，不在白名单内时返回 None。"""
    group_id = event_group_id(event)
    if not group_id:
        return None
    allowlist = _id_allowlist(cfg, "group_allowlist")
    return group_id if not allowlist or group_id in allowlist else None


def _message_filtered(cfg: dict[str, Any], event: Any) -> bool:
    """命中屏蔽词或正则的消息既不进入 AI，也不写历史。"""
    text = event_plain_text(event)
    if not text:
        return False
    settings = cfg.get("message_receive")
    if not isinstance(settings, dict):
        return False
    words = settings.get("ban_words") or []
    if any(word and word in text for word in words if isinstance(word, str)):
        return True
    for pattern in settings.get("ban_msgs_regex") or []:
        try:
            if isinstance(pattern, str) and pattern and re.search(pattern, text):
                return True
        except re.error:
            continue
    return False


def _reply_timing(cfg: dict[str, Any]) -> dict[str, Any]:
    """回复时机配置；缺失时按默认值。"""
    value = cfg.get("reply_timing")
    return value if isinstance(value, dict) else {}


def _match_context(cfg: dict[str, Any], bot: Any, event: Any) -> bool:
    """按会话类型判断是否允许进入 AI 兜底；关闭的必回复项交给上下文记录。"""
    if event_group_id(event):
        if not _allowed_group_id(cfg, event):
            return False
        timing = _reply_timing(cfg)
        if _is_to_me(event):
            return bool(timing.get("inevitable_at_reply", True))
        if _persona_trigger(cfg, bot, event):
            return bool(timing.get("mentioned_bot_reply", True))
        return False
    if not bool(cfg["private_enabled"]):
        return False
    allowlist = _id_allowlist(cfg, "private_allowlist")
    if allowlist and event_user_id(event) not in allowlist:
        return False
    return True


def _should_record_context(cfg: dict[str, Any], bot: Any, event: Any) -> bool:
    """不能进入 AI 回复的普通群消息写历史，包括关闭必回复的提及。"""
    if event_group_id(event):
        if not _allowed_group_id(cfg, event):
            return False
        timing = _reply_timing(cfg)
        if _is_to_me(event) and timing.get("inevitable_at_reply", True):
            return False
        if _persona_trigger(cfg, bot, event) and timing.get("mentioned_bot_reply", True):
            return False
        return True
    if not bool(cfg["private_enabled"]):
        return False
    allowlist = _id_allowlist(cfg, "private_allowlist")
    if allowlist and event_user_id(event) not in allowlist:
        return False
    return False


async def _ai_fallback_rule(bot: Bot, event: Event) -> bool:
    """AI 兜底匹配规则。"""
    if not should_process_fxbot_message(bot, event):
        return False
    cfg = _chat_cfg()
    if not bool(cfg["enabled"]) or _message_filtered(cfg, event):
        return False
    if not getattr(event, _PROACTIVE_ATTR, False) and not _match_context(cfg, bot, event):
        return False
    text = message_text(event)
    if not text and not _has_image(event) and not _has_voice(event):
        return False
    return True


async def _context_rule(bot: Bot, event: Event) -> bool:
    """记录无需回复的群聊或私聊消息，作为后续对话的上下文。"""
    if not should_process_fxbot_message(bot, event):
        return False
    cfg = _chat_cfg()
    if not bool(cfg["enabled"]) or history_limit(cfg, bool(event_group_id(event))) <= 0:
        return False
    if _message_filtered(cfg, event):
        return False
    if not _should_record_context(cfg, bot, event):
        return False
    text = message_text(event)
    has_image = _has_image(event)
    has_voice = _has_voice(event)
    if not text and not has_image and not has_voice:
        return False
    return True


# 优先级高于 AI 兜底：先记录群聊上下文，再由兜底决定是否回复
context_router = on_message(rule=Rule(_context_rule), priority=98, block=False)
ai_router = on_message(rule=Rule(_ai_fallback_rule), priority=99, block=True)


@context_router.handle()
async def _handle_context(bot: Bot, event: Event) -> None:
    """把群聊或私聊的上下文消息写入 AI 会话历史。"""
    identity = event_identity(bot, event)
    if not chat_service.session_store.mark_seen(identity.session_id, identity.dedupe_key):
        return
    quote_counter(identity.session_id).record(identity.message_id or "", normalize_id(event_user_id(event)) or "")
    emoji_module.collect_in_background(identity.session_id, event)
    has_image = _has_image(event)
    text = message_text(event)
    user_id = normalize_id(event_user_id(event)) or ""
    if not text and not has_image and _has_voice(event):
        # 纯语音先转写再入历史，转写失败时留占位
        text = await transcribe_event(event)
    images = image_sources_for_event(event) if has_image else []
    # 上下文消息只做廉价提取：昵称取消息段里现成的，不额外查成员接口
    mentions = await collect_mentions(bot, event, event_group_id(event), resolve_names=False)
    await chat_service.record_group_message(
        identity.session_id,
        user_id,
        event_user_name(event, user_id),
        text,
        is_group=bool(event_group_id(event)),
        images=images,
        has_image=has_image,
        mentions=mentions,
        dedupe_key=identity.dedupe_key,
        platform=identity.platform,
        event_id=identity.event_id,
        message_id=identity.message_id,
        reply_to_message_id=identity.reply_to_message_id,
        memory_images=memory_image_sources(event),
    )
    if focus_mode_manager.enabled() or focus_mode_manager.attached(identity.session_id):
        focus_request = await adapt_message_event(bot, event, trigger_kind="mention")
        # 这条消息刚由 record_group_message 写入历史，Focus 回复时不能再写一遍
        focus_request.metadata["history_recorded"] = True
        await _focus_or_detach(bot, event, focus_request, is_at=not focus_request.is_group)
        if focus_mode_manager.is_enabled_for_request(focus_request):
            return
    if event_group_id(event):
        request = await adapt_message_event(bot, event, trigger_kind="proactive")
        request.accepted_epoch = chat_service.session_store.epoch(identity.session_id)
        request.activity_seq = chat_service.session_store.activity(identity.session_id)
        request.metadata.update({"history_recorded": True, "history_appended": True})
        has_at_bot, at_other = mention_flags(bot, event)
        request.metadata.update({"has_at_bot": has_at_bot, "at_other": at_other})
        matcher = FocusMatcher(bot, extract_message_target(event))
        runtime = default_runtime_factory.create(bot=bot, event=event, matcher=matcher)
        await normal_scheduler.submit(chat_service, request, runtime)


async def _register_focus(bot: Bot, event: Event, request: ChatRequest, *, is_at: bool) -> None:
    """把入站消息交给共享组；是否回复由共享规划器统一决定。"""
    member = FocusMember(
        session_id=request.session_id,
        bot=bot,
        event=event,
        target=extract_message_target(event),
        request=request,
        is_group=request.is_group,
    )
    await focus_mode_manager.register(member, request, is_at=is_at)


async def _focus_or_detach(bot: Bot, event: Event, request: ChatRequest, *, is_at: bool) -> bool:
    """Focus 接管时登记并返回 True；已不满足配置时让该聊天退回普通对话。"""
    if focus_mode_manager.is_enabled_for_request(request):
        await _register_focus(bot, event, request, is_at=is_at)
        return True
    if focus_mode_manager.attached(request.session_id):
        await focus_mode_manager.detach(request.session_id)
    return False


@ai_router.handle()
async def _handle_ai_fallback(bot: Bot, event: Event, matcher: Matcher) -> None:
    """处理 AI 兜底消息；首个触发消息等完防抖窗口后统一生成一轮。"""
    identity = event_identity(bot, event)
    if not chat_service.session_store.mark_seen(identity.session_id, identity.dedupe_key):
        logger.debug("[Chat] 忽略重复投递的 AI 消息")
        return
    quote_counter(identity.session_id).record(identity.message_id or "", normalize_id(event_user_id(event)) or "")
    emoji_module.collect_in_background(identity.session_id, event)
    if getattr(event, _PROACTIVE_ATTR, False):
        trigger_kind: TriggerKind = "proactive"
    else:
        trigger_kind = "private" if not event_group_id(event) else "mention" if _is_to_me(event) else "persona"
    # 序号按到达顺序分配：适配会下载图片、转写语音，放到适配之后会让慢消息拿到更新的序号
    await chat_service.ensure_hydrated(identity.session_id, is_group=bool(event_group_id(event)))
    seq_owner = "\0proactive" if trigger_kind == "proactive" else normalize_id(event_user_id(event)) or ""
    trigger_seq = chat_service.session_store.next_seq(identity.session_id, seq_owner)
    accepted_epoch = chat_service.session_store.epoch(identity.session_id)
    activity_seq = chat_service.session_store.activity(identity.session_id)
    request = await adapt_message_event(bot, event, trigger_kind=trigger_kind)
    if await _focus_or_detach(bot, event, request, is_at=trigger_kind in {"mention", "persona", "private"}):
        # 共享组自己负责防抖、规划与发送，这里不再走普通轮次
        return
    if not request.text.strip() and not request.images:
        logger.debug("[Chat] 这条消息没有可回复的内容，跳过本轮")
        return
    # 序号与代次在进入 turn_lock 前捕获，用于丢弃被新消息或清空取代的旧回复
    request.trigger_seq = trigger_seq
    request.trigger_owner = seq_owner
    request.accepted_epoch = accepted_epoch
    request.activity_seq = activity_seq
    runtime = default_runtime_factory.create(bot=bot, event=event, matcher=matcher)
    await chat_service.record_group_message(
        request.session_id,
        request.user_id,
        request.user_name,
        request.text,
        is_group=request.is_group,
        images=request.images,
        has_image=bool(request.images),
        mentions=request.mentions,
        dedupe_key=request.dedupe_key,
        platform=request.platform,
        event_id=request.event_id,
        message_id=request.message_id,
        reply_to_message_id=request.reply_to_message_id,
        occurred_at=request.occurred_at,
        memory_images=list(zip(request.metadata.get("raw_image_paths", []), request.metadata.get("raw_images", []))),
    )
    request.metadata.update({"history_recorded": True, "history_appended": True})
    has_at_bot, at_other = mention_flags(bot, event)
    request.metadata.update({"has_at_bot": has_at_bot, "at_other": at_other})
    await normal_scheduler.submit(chat_service, request, runtime)
