"""回复分段发送、引用与表情补发。

发送顺序与打字节奏对齐 MaiBot（commit 062d45c，`src/chat/message_receive/uni_message_sender.py`）。
"""

from __future__ import annotations

import random
import asyncio
from time import perf_counter
from typing import Any
from functools import partial

from nonebot import logger

from ..typo import apply_typo, typo_config
from ..emoji import mark_used, emoji_by_hash
from ..trace import turn_trace
from ..types import ChatRequest, ChatResponse, is_proactive
from ..voice import synthesize, tts_enabled
from ..memory import state as memory_state
from ..session import history_enabled
from ...adapter import build_message, supports_reply, build_reply_segment, build_message_segment
from ..memory.models import ChatTurnItem, utc_now_naive
from ..work_schedule import can_reply
from ..behavior.quote import decide_quote, quote_counter, quote_frequency
from ..behavior.mentions import split_mentions, reply_at_enabled
from ..behavior.response import typing_time, process_response
from ..memory.repository import chat_repository
from ..amemorix.writeback import chat_writeback
from ..behavior.reply_gate import default_reply_gate_registry


def item_id(response: ChatResponse, index: int) -> int | None:
    """取第 index 段对应的轮内条目 ID。"""
    ids = response.metadata.get("turn_item_ids") or []
    return int(ids[index]) if 0 <= index < len(ids) else None


def parts(response: ChatResponse) -> list[str]:
    """发送分段；持久化关闭时按同一规则重新拆分。"""
    stored = response.metadata.get("reply_parts")
    if isinstance(stored, list) and stored:
        return [str(part) for part in stored]
    return [segment.text for segment in process_response(response.text)]


def _receipt_id(receipt: Any) -> str | None:
    """取发送回执里的平台消息 ID，兼容字典与使用 id 字段的适配器。"""
    if isinstance(receipt, dict):
        value = receipt.get("message_id") or receipt.get("id")
    else:
        value = getattr(receipt, "message_id", None)
        if value is None:
            value = getattr(receipt, "id", None)
    return str(value) if value is not None else None


def _alive(service: Any, request: ChatRequest, response: ChatResponse) -> bool:
    """附加输出（语音、纠错、表情）与正文共用同一轮次令牌。"""
    return can_reply() and service.is_current(request.session_id, response.epoch) and service.is_latest(request)


def _always_alive() -> bool:
    return True


def _bot_id(runtime: Any) -> str:
    """Bot 自身发送账号；引用计数用它判断上一条是不是自己说的。"""
    return str(getattr(getattr(runtime, "bot", None), "self_id", "") or "")


def _should_quote(request: ChatRequest, runtime: Any, part: str) -> bool:
    """按群聊/私聊引用频率抽签。"""
    target = str(request.metadata.get("reply_target_id") or request.message_id or "")
    if not target or (is_proactive(request) and not request.metadata.get("planner_reply")):
        return False
    context = quote_counter(request.session_id).snapshot(target)
    quote, _ = decide_quote(
        context=context,
        bot_user_id=_bot_id(runtime),
        text=part,
        frequency=quote_frequency(request.is_group),
        is_group=request.is_group,
    )
    return quote


def first_payload(service: Any, request: ChatRequest, runtime: Any, part: str, user_ids: list[str]) -> Any:
    """首段可带引用段与开头的 @ 段；构造失败时降级为纯文本。"""
    request.metadata["sent_quote_id"] = ""
    bot = getattr(runtime, "bot", None)
    if bot is None:
        return part
    segments: list[Any] = []
    if _should_quote(request, runtime, part):
        try:
            if supports_reply(bot):
                target = str(request.metadata.get("reply_target_id") or request.message_id or "")
                segments.append(build_reply_segment(bot, target))
                request.metadata["sent_quote_id"] = target
        except Exception:
            logger.opt(exception=True).debug("[Chat] 引用构造失败，降级为纯文本")
    if user_ids:
        try:
            segments.extend(build_message_segment(bot, "at", user_id) for user_id in user_ids)
        except Exception:
            logger.opt(exception=True).debug("[Chat] @ 段构造失败，降级为纯文本")
            request.metadata["sent_quote_id"] = ""
            return part
    if not segments:
        return part
    try:
        segments.append(build_message_segment(bot, "text", part))
        return build_message(bot, *segments)
    except Exception:
        logger.opt(exception=True).debug("[Chat] 首段构造失败，降级为纯文本")
        request.metadata["sent_quote_id"] = ""
        return part


async def send_pictures(service: Any, request: ChatRequest, response: ChatResponse, runtime: Any, matcher: Any) -> None:
    """正文成功后依序附图，成功回执才进入可见历史，清空后停止发送。"""
    bot = getattr(runtime, "bot", None)
    if bot is None:
        return
    for index, picture in enumerate(response.metadata.get("attach_pictures", [])):
        if index < int(response.metadata.get("sent_pictures", 0)):
            continue
        if not _alive(service, request, response):
            return
        persisted_id = None
        if (turn_id := response.metadata.get("turn_db_id")) and memory_state.is_ready():
            persisted_id = await chat_repository.append_turn_item(
                ChatTurnItem(
                    tenant=request.session_id,
                    turn_id=turn_id,
                    item_seq=len(parts(response)) + 1 + index,
                    role="assistant",
                    content="[图片]",
                    visible=False,
                    send_status="pending",
                ),
                generation=response.epoch,
            )
        if not _alive(service, request, response):
            return
        try:
            receipt = await matcher.send(build_message(bot, build_message_segment(bot, "image", picture)))
        except Exception:
            logger.opt(exception=True).warning("[Chat] 回复附图发送失败")
            if persisted_id is not None:
                await chat_repository.update_turn_item(
                    request.session_id, persisted_id, generation=response.epoch, send_status="failed", visible=False
                )
                await chat_repository.update_turn(
                    request.session_id,
                    request.turn_id,
                    generation=response.epoch,
                    status="partial",
                    error="附图发送失败",
                )
            return
        response.metadata["sent_pictures"] = index + 1
        attachment = ChatResponse(
            text="[图片]",
            epoch=response.epoch,
            metadata={"reply_parts": ["[图片]"], "turn_item_ids": [persisted_id] if persisted_id is not None else []},
        )
        request.metadata["sent_quote_id"] = ""
        await service.mark_sent(request, attachment, 0, _receipt_id(receipt), content="[图片]")


async def send_voice(runtime: Any, matcher: Any, text: str, alive: Any = None) -> bool:
    """给刚发出的文字补一条同内容语音；未开启、失败或轮次已失效时只跳过。"""
    if not tts_enabled():
        return False
    audio = await synthesize(text)
    bot = getattr(runtime, "bot", None)
    if audio is None or bot is None or not (alive or _always_alive)():
        return False
    try:
        segment = build_message_segment(bot, "record", audio)
        await matcher.send(build_message(bot, segment))
    except Exception:
        logger.opt(exception=True).debug("[Chat] 语音发送失败，已跳过")
        return False
    return True


def correction_payload(runtime: Any, text: str, message_id: str | None) -> Any:
    """纠正消息可引用刚发出的错字消息；构造失败时降级为纯文本。"""
    bot = getattr(runtime, "bot", None)
    if bot is None or not message_id:
        return text
    try:
        return build_message(bot, build_reply_segment(bot, message_id), build_message_segment(bot, "text", text))
    except Exception:
        logger.opt(exception=True).debug("[Chat] 纠正引用构造失败，降级为纯文本")
        return text


def _quote_correction() -> bool:
    """按错字配置决定纠正消息是否引用错字那条。"""
    cfg = typo_config()
    chance = cfg.get("correction_quote_probability", 1.0)
    return bool(
        cfg.get("enable_correction_quote", True)
        and isinstance(chance, (int, float))
        and not isinstance(chance, bool)
        and random.random() < chance
    )


async def send_correction(runtime: Any, matcher: Any, text: str, message_id: str | None, alive: Any = None) -> None:
    """在错字消息后补一条纠正；等待期间轮次失效或发送失败都只跳过。"""
    await asyncio.sleep(typing_time(text))
    if not (alive or _always_alive)():
        return
    payload = correction_payload(runtime, text, message_id if _quote_correction() else None)
    try:
        await matcher.send(payload)
    except Exception:
        logger.opt(exception=True).debug("[Chat] 纠正消息发送失败，已跳过")


async def deliver_emoji(
    service: Any,
    request: ChatRequest,
    response: ChatResponse,
    runtime: Any,
    matcher: Any,
    row: Any,
    sequence: int,
    *,
    target_id: str = "",
) -> bool:
    """发送成功后记录表情的使用、平台回执和可见历史。"""
    if response.metadata.get("emoji_sent") or not _alive(service, request, response):
        return False
    bot = getattr(runtime, "bot", None)
    if bot is None:
        return False
    content = f"[表情包: {row.description}]" if row.description else "[表情包]"
    identifier = None
    if (turn_id := response.metadata.get("turn_db_id")) and memory_state.is_ready():
        identifier = await chat_repository.append_turn_item(
            ChatTurnItem(
                tenant=request.session_id,
                turn_id=turn_id,
                item_seq=sequence,
                role="assistant",
                content=content,
                payload=f'{{"emoji_hash":"{row.hash}"}}',
                visible=False,
                send_status="pending",
            ),
            generation=response.epoch,
        )
        if identifier is None:
            return False
    if not _alive(service, request, response):
        if identifier is not None:
            await chat_repository.update_turn_item(
                request.session_id, identifier, generation=response.epoch, send_status="skipped", visible=False
            )
        return False
    try:
        segment = build_message_segment(bot, "image", row.path)
        segments = [segment]
        quoted = False
        if target_id and supports_reply(bot):
            segments.insert(0, build_reply_segment(bot, target_id))
            quoted = True
        receipt = await matcher.send(build_message(bot, *segments))
    except Exception:
        logger.opt(exception=True).warning("[Chat] 表情发送失败")
        if identifier is not None:
            await chat_repository.update_turn_item(
                request.session_id, identifier, generation=response.epoch, send_status="failed", visible=False
            )
            await chat_repository.update_turn(
                request.session_id,
                request.turn_id,
                generation=response.epoch,
                status="failed" if sequence == 1 else "partial",
                error="表情发送失败",
            )
        return False
    response.metadata["emoji_sent"] = True
    await mark_used(row)
    visible = ChatResponse(
        text=content,
        epoch=response.epoch,
        metadata={"reply_parts": [content], "turn_item_ids": [identifier] if identifier is not None else []},
    )
    request.metadata["sent_quote_id"] = target_id if quoted else ""
    receipt_id = _receipt_id(receipt)
    await service.mark_sent(request, visible, 0, receipt_id, content=content)
    if receipt_id:
        quote_counter(request.session_id).record(receipt_id, _bot_id(runtime))
    return True


async def send_emoji(
    service: Any,
    request: ChatRequest,
    response: ChatResponse,
    runtime: Any,
    matcher: Any,
    sent: int,
    alive: Any = None,
) -> None:
    """正文发送后只交付 Planner 明确指定的表情附件。"""
    alive = alive or _always_alive
    bot = getattr(runtime, "bot", None)
    if bot is None or not alive():
        return
    attached = str(response.metadata.get("attach_emoji") or "")
    if attached:
        row = await emoji_by_hash(request.session_id, attached)
        if row is not None and alive():
            await deliver_emoji(
                service,
                request,
                response,
                runtime,
                matcher,
                row,
                len(parts(response)) + len(response.metadata.get("attach_pictures", [])) + 1,
            )
        return


async def send_parts(service: Any, request: ChatRequest, response: ChatResponse, runtime: Any, matcher: Any) -> str:
    """发送回复并将交付终态补入生成阶段的追踪。"""
    started = perf_counter()
    delivery: dict[str, Any] = {"sent_parts": 0, "failed_index": None, "cancelled": False, "error": ""}
    try:
        return await _send_parts(service, request, response, runtime, matcher, delivery)
    except asyncio.CancelledError:
        delivery["cancelled"] = True
        raise
    except Exception as exc:
        delivery["failed_index"] = delivery["sent_parts"]
        delivery["error"] = f"{type(exc).__name__}: {exc}"[:500]
        raise
    finally:
        await record_send_trace(request, response, **delivery, send_ms=int((perf_counter() - started) * 1000))


async def record_send_trace(
    request: ChatRequest,
    response: ChatResponse,
    *,
    sent_parts: int = 0,
    failed_index: int | None = None,
    cancelled: bool = False,
    send_ms: int = 0,
    error: str = "",
) -> None:
    """补写发送追踪；失败不影响对话。"""
    state = turn_trace(request)
    delivery = {
        "sent_parts": sent_parts,
        "total_parts": len(parts(response)),
        "failed_index": failed_index,
        "cancelled": cancelled,
        "send_ms": send_ms,
    }
    budget: dict = {"send": delivery}
    if state is not None:
        provider = next((stage["provider"] for stage in reversed(state.stages) if stage["kind"] == "replyer"), "")
        state.add_stage(
            "send",
            "发送回复",
            send_ms,
            provider=provider,
            error=error,
            target_message_id=request.message_id or "",
            send=delivery,
        )
        if not state.saved:
            return
        budget = state.snapshot()
    if not memory_state.is_ready() or not history_enabled(request.is_group):
        return
    try:
        await chat_repository.update_trace(
            request.session_id,
            state.turn_id if state is not None else request.turn_id,
            budget=budget,
            latency_ms=send_ms,
            total_latency_ms=budget["timings"]["total_ms"] if state is not None else None,
            error=error,
        )
    except Exception:
        logger.opt(exception=True).debug("[Chat] 更新发送 trace 失败")


async def _send_parts(
    service: Any, request: ChatRequest, response: ChatResponse, runtime: Any, matcher: Any, delivery: dict[str, Any]
) -> str:
    """逐段发送；同租户发送期间不交错，任何一段失效后不再继续。"""
    send_queue = parts(response)
    if not send_queue:
        return ""
    alive = partial(_alive, service, request, response)
    sent_texts: list[str] = []
    async with service.session_store.send_lock(request.session_id):
        for index, part in enumerate(send_queue):
            if index > 0:
                await asyncio.sleep(typing_time(part))
            if not alive():
                delivery["cancelled"] = True
                await service.mark_send_aborted(request, response, index)
                break
            await service.mark_sending(request, response, index)
            # 标记发送中会写库，期间同一用户可能已经发来新消息
            if not alive():
                delivery["cancelled"] = True
                await service.mark_send_aborted(request, response, index)
                break
            user_ids: list[str] = []
            body = part
            if index == 0:
                if request.metadata.get("planner_reply"):
                    user_ids = request.metadata.get("reply_at_users", []) if reply_at_enabled() else []
                else:
                    user_ids, body = split_mentions(part, request)
            typo = apply_typo(body)
            payload = first_payload(service, request, runtime, typo.text, user_ids) if index == 0 else typo.text
            try:
                receipt = await matcher.send(payload)
            except Exception as exc:
                logger.opt(exception=True).warning(f"[Chat] 回复第 {index + 1} 段发送失败: {exc}")
                delivery["failed_index"] = index
                delivery["error"] = f"{type(exc).__name__}: {exc}"[:500]
                await service.mark_send_failed(request, response, exc, index)
                break
            delivery["sent_parts"] += 1
            # 首段已真实发出就计一次，后续持久化失败、分段取消或关闭历史都不撤销发送事实。
            if not sent_texts and request.is_group:
                default_reply_gate_registry.get(request.session_id).record_reply()
            receipt_id = _receipt_id(receipt)
            await service.mark_sent(request, response, index, receipt_id, content=typo.text)
            if receipt_id:
                quote_counter(request.session_id).record(receipt_id, _bot_id(runtime))
            sent_texts.append(typo.text)
            await send_voice(runtime, matcher, body, alive)
            if typo.correction:
                await send_correction(runtime, matcher, typo.correction, receipt_id, alive)
        if sent_texts:
            if delivery.get("failed_index") is None and not delivery.get("cancelled"):
                await send_pictures(service, request, response, runtime, matcher)
            await send_emoji(service, request, response, runtime, matcher, len(sent_texts), alive)
    reply_text = "\n".join(sent_texts)
    if sent_texts and history_enabled(request.is_group):
        chat_writeback.on_reply(
            request.session_id,
            platform=request.platform,
            user_id=request.user_id,
            user_name=request.user_name,
            group_id=str(request.group_id or ""),
            reply_text=reply_text,
            reply_to_message_id=str(request.metadata.get("reply_target_id") or request.message_id or ""),
            occurred_at=utc_now_naive(),
        )
    return reply_text
