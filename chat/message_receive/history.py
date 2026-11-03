"""历史水合、落库与轮次状态。

持久化边界对齐 MaiBot（commit 062d45c，`src/chat/message_receive/chat_manager.py`）：
原始消息进入历史，生成文本只有真正发出后才可见。
"""

from __future__ import annotations

import json
import hashlib
from uuid import uuid4
from typing import Any
from datetime import datetime

from nonebot import logger

from ..types import MentionRef, ChatRequest, ChatResponse, speaker_label
from .sender import parts, item_id
from ..memory import state as memory_state
from ..vision import describe_sources, vision_configured
from ..session import history_enabled
from ...utils.tz import to_naive_utc
from ..providers import LLMResponse
from ..memory.models import ChatTurn, ChatMessage, ChatTurnItem, utc_now_naive
from ..replyer.prompt import strip_images, mentions_line, build_user_content
from ..amemorix.people import remember_person
from ..message_context import message_metadata, restore_reference
from ..memory.repository import chat_repository
from ..providers.routing import fallback_provider
from ..amemorix.writeback import chat_writeback


async def hydrate(service: Any, session_id: str, limit: int) -> None:
    """首次访问会话时从数据库恢复最近历史，不覆盖已到的新消息。"""
    if limit <= 0 or service.session_store.is_hydrated(session_id):
        return
    if not memory_state.is_ready():
        service.session_store.mark_hydrated(session_id)
        return
    async with service.session_store.lock(session_id):
        if service.session_store.is_hydrated(session_id):
            return
        if service.session_store.get(session_id):
            service.session_store.mark_hydrated(session_id)
            return
        try:
            state = await chat_repository.get_session_state(session_id)
            if state is not None:
                service.session_store.set_epoch(session_id, state.generation)
            history = await chat_repository.get_recent_messages(session_id, max(1, (limit + 1) // 2))
        except Exception:
            logger.opt(exception=True).warning("[Chat] 历史恢复失败，本轮从内存开始")
            return
        service.session_store.append_many(session_id, history[-limit:], limit=limit)
        if reference := restore_reference(history):
            service.session_store.append(session_id, reference, limit=limit)
        service.session_store.mark_hydrated(session_id)


def _message_row(request: ChatRequest, content: str) -> ChatMessage:
    """构造原始消息行；合并批次里的每条消息都先按原样落库。"""
    quote_id = str(
        (request.metadata.get("quote_snapshot") or {}).get("message_id") or request.reply_to_message_id or ""
    )
    return ChatMessage(
        tenant=request.session_id,
        kind="user",
        platform=request.platform,
        platform_event_id=request.event_id,
        platform_message_id=request.message_id,
        reply_to_message_id=quote_id or None,
        sender_id=request.user_id,
        sender_name=request.user_name,
        content=content,
        payload=json.dumps(
            {
                "has_image": bool(request.images),
                "image_memory": request.metadata.get("image_memory", []),
                "context": message_metadata(
                    timestamp=request.occurred_at,
                    sender_id=request.user_id,
                    sender_name=request.user_name,
                    quote_id=quote_id,
                    at_bot=bool(request.metadata.get("has_at_bot")),
                    sender_role=request.metadata.get("sender_role") if request.is_group else None,
                    quote_snapshot=request.metadata.get("quote_snapshot"),
                ),
            },
            ensure_ascii=False,
        ),
        source="live",
        dedupe_key=request.dedupe_key,
        occurred_at=to_naive_utc(request.occurred_at),
    )


async def _track_message(
    session_id: str,
    platform: str,
    user_id: str,
    user_name: str,
    group_id: str = "",
    occurred_at: datetime | None = None,
) -> None:
    """登记发言人并投递摘要任务；A_Memorix 未启用时下游自行短路。"""
    if not memory_state.is_ready() or not user_id:
        return
    await remember_person(
        session_id,
        platform,
        user_id,
        user_name,
        group_id=group_id,
        group_cardname=user_name if group_id else "",
    )
    chat_writeback.on_message(
        session_id,
        platform=platform,
        user_id=user_id,
        user_name=user_name,
        group_id=group_id,
        occurred_at=occurred_at,
    )


async def _prepare_image_memory(request: ChatRequest) -> None:
    """原始图片缓存只准备一次，避免等待后重新规划反复下载。"""
    if request.metadata.get("image_memory") or request.metadata.get("history_recorded"):
        return
    from ..amemorix.image_writeback import image_writeback

    request.metadata["image_memory"] = await image_writeback.prepare(
        request.session_id, request.metadata.get("raw_images", request.images), request.metadata.get("raw_image_paths")
    )


async def _enqueue_image_memory(request: ChatRequest) -> None:
    """原图和原始消息均已持久化后登记可恢复的图片任务。"""
    if not request.metadata.get("image_memory"):
        return
    from ..amemorix.image_writeback import image_writeback

    await image_writeback.enqueue(request.session_id, request.message_id or request.dedupe_key)


async def persist_batch(requests: list[ChatRequest]) -> None:
    """缓冲里的原始消息逐条落库；合并内容只作为本轮 user 条目。"""
    for request in requests:
        if not history_enabled(request.is_group):
            continue
        content = strip_images([{"role": "user", "content": build_user_content(request)}])[0]["content"]
        await _prepare_image_memory(request)
        try:
            await chat_repository.append_message(_message_row(request, str(content)), generation=request.accepted_epoch)
            await _enqueue_image_memory(request)
        except Exception:
            logger.opt(exception=True).warning("[Chat] 缓冲消息落库失败，跳过剩余消息")
            return
        await _track_message(
            request.session_id,
            request.platform,
            request.user_id,
            request.user_name,
            str(request.group_id or ""),
            request.occurred_at,
        )


async def persist_turn_start(request: ChatRequest, content: str, epoch: int) -> tuple[int, int | None] | None:
    """写入用户消息、轮次和用户条目；失败只降级本轮持久化。"""
    if not memory_state.is_ready() or not history_enabled(request.is_group):
        return None
    await _prepare_image_memory(request)
    if request.accepted_epoch is None:
        request.accepted_epoch = epoch
    payload = json.dumps({"has_image": True}) if request.images else "{}"
    trigger_ids = request.source_message_ids or [request.message_id or request.dedupe_key]
    author_ids = request.source_author_ids or [request.user_id]
    turn = ChatTurn(
        tenant=request.session_id,
        turn_key=request.turn_id,
        generation=epoch,
        trigger_message_ids=json.dumps(trigger_ids, ensure_ascii=False),
        author_ids=json.dumps(author_ids, ensure_ascii=False),
        trigger_kind=request.trigger_kind,
        request_digest=hashlib.sha1(content.encode("utf-8")).hexdigest(),
        status="generating",
        started_at=utc_now_naive(),
    )
    try:
        turn_id = await chat_repository.upsert_turn(turn)
        if turn_id is None:
            return None
        # 合并批次的原始消息已由 persist_batch 写入，这里只写合并后的 user 条目
        recorded = request.batched or bool(request.metadata.get("history_recorded"))
        message_id = (
            None if recorded else await chat_repository.append_message(_message_row(request, content), generation=epoch)
        )
        if message_id is not None:
            # 等待后重新规划会再次走到这里，原始消息不再重复落库
            request.metadata["history_recorded"] = True
            await _enqueue_image_memory(request)
        await chat_repository.append_turn_item(
            ChatTurnItem(
                tenant=request.session_id,
                turn_id=turn_id,
                item_seq=0,
                role="user",
                content=content,
                payload=payload,
                source_message_id=message_id,
                visible=True,
                send_status="sent",
            ),
            generation=epoch,
        )
    except Exception:
        logger.opt(exception=True).warning("[Chat] 轮次落库失败，本轮仅用内存历史")
        return None
    if message_id is not None:
        await _track_message(
            request.session_id,
            request.platform,
            request.user_id,
            request.user_name,
            str(request.group_id or ""),
            request.occurred_at,
        )
    return turn_id, message_id


async def persist_turn_end(
    request: ChatRequest,
    persisted: tuple[int, int | None] | None,
    response: LLMResponse,
    provider_name: str,
    content: str,
    descriptions: list[str],
    parts_to_send: list[str],
    generated: str | None = None,
) -> list[int] | None:
    """生成完成后写 generated_text 与逐段待发送的助手条目。"""
    if persisted is None or not memory_state.is_ready() or not history_enabled(request.is_group):
        return None
    turn_id, message_id = persisted
    text = generated if generated is not None else response.content or ""
    try:
        if not (response.content or "").strip():
            await chat_repository.update_turn(
                request.session_id,
                request.turn_id,
                generation=request.accepted_epoch,
                status="failed",
                provider=provider_name,
                error="空响应",
                finished_at=utc_now_naive(),
            )
            return None
        await chat_repository.update_turn(
            request.session_id,
            request.turn_id,
            generation=request.accepted_epoch,
            status="generated",
            generated_text=text,
            provider=provider_name,
            finished_at=utc_now_naive(),
        )
        if descriptions and message_id is not None:
            await chat_repository.update_message_content(
                request.session_id, message_id, content, turn_key=request.turn_id, generation=request.accepted_epoch
            )
        item_ids: list[int] = []
        for index, part in enumerate(parts_to_send):
            stored_item_id = await chat_repository.append_turn_item(
                ChatTurnItem(
                    tenant=request.session_id,
                    turn_id=turn_id,
                    item_seq=1 + index,
                    part_index=index,
                    role="assistant",
                    content=part,
                    payload=json.dumps(
                        {
                            "context": {
                                **message_metadata(
                                    timestamp=utc_now_naive(),
                                    sender_id=str(request.metadata.get("bot_id") or ""),
                                    sender_name=str(request.metadata.get("bot_name") or ""),
                                    sender_role=request.metadata.get("bot_group_role") if request.is_group else None,
                                ),
                                "_self_message": True,
                            }
                        },
                        ensure_ascii=False,
                    ),
                    visible=False,
                    send_status="pending",
                ),
                generation=request.accepted_epoch,
            )
            if stored_item_id is None:
                return None
            item_ids.append(stored_item_id)
        return item_ids
    except Exception:
        logger.opt(exception=True).warning("[Chat] 助手回复落库失败")
        return None


async def mark_sending(request: ChatRequest, response: ChatResponse, index: int = 0) -> None:
    """发送前把轮次与第 index 段标为发送中。"""
    if not memory_state.is_ready() or not history_enabled(request.is_group):
        return
    turn_item_id = item_id(response, index)
    try:
        if turn_item_id is not None:
            await chat_repository.update_turn_item(
                request.session_id, turn_item_id, generation=response.epoch, send_status="sending"
            )
        await chat_repository.update_turn(
            request.session_id, request.turn_id, generation=response.epoch, status="sending"
        )
    except Exception:
        logger.opt(exception=True).warning("[Chat] 记录发送中状态失败")


async def mark_sent(
    service: Any,
    request: ChatRequest,
    response: ChatResponse,
    index: int = 0,
    platform_message_id: str | None = None,
    *,
    content: str | None = None,
    limit: int,
) -> None:
    """发送成功后把该段写入可见历史；重复调用不重复追加。content 是实际发出的文字。"""
    if limit <= 0 or not service.is_current(request.session_id, response.epoch):
        logger.debug("[Chat] 跳过过期发送结果: tenant={} generation={}", request.session_id, response.epoch)
        return
    repeat = False
    sent_parts = parts(response)
    text = content if content is not None else sent_parts[index] if 0 <= index < len(sent_parts) else response.text
    context = {
        **message_metadata(
            timestamp=utc_now_naive(),
            sender_id=str(request.metadata.get("bot_id") or ""),
            sender_name=str(request.metadata.get("bot_name") or ""),
            quote_id=str(request.metadata.get("sent_quote_id") or "") if index == 0 else "",
            sender_role=request.metadata.get("bot_group_role") if request.is_group else None,
        ),
        "_self_message": True,
    }
    turn_item_id = item_id(response, index)
    if turn_item_id is not None and memory_state.is_ready():
        try:
            repeat = not await chat_repository.mark_turn_item_sent(
                request.session_id,
                turn_item_id,
                platform_message_id,
                generation=response.epoch,
                content=text,
                context=context,
            )
        except Exception:
            logger.opt(exception=True).warning("[Chat] 记录发送结果失败")
    if repeat or not text or not service.is_current(request.session_id, response.epoch):
        return
    async with service.session_store.lock(request.session_id):
        if service.is_current(request.session_id, response.epoch):
            entry = {
                "role": "assistant",
                "content": text,
                **context,
                "_logical_turn_id": str(request.metadata.get("planner_parent_turn_id") or request.turn_id),
            }
            if platform_message_id:
                entry["_source_ids"] = [platform_message_id]
            service.session_store.append(request.session_id, entry, limit=limit)


async def _skip_items(request: ChatRequest, response: ChatResponse, start: int) -> None:
    """把未发送的段标成 skipped，避免留下待发送状态。"""
    for index in range(start, len(parts(response))):
        turn_item_id = item_id(response, index)
        if turn_item_id is not None:
            await chat_repository.update_turn_item(
                request.session_id, turn_item_id, generation=response.epoch, send_status="skipped", visible=False
            )


async def mark_send_aborted(
    request: ChatRequest, response: ChatResponse, index: int = 0, error: str = "发送前已被新触发取代"
) -> None:
    """发送前复核失败：从第 index 段起作废，至少有一段成功时轮次标为部分发送。"""
    if not memory_state.is_ready() or not history_enabled(request.is_group):
        return
    try:
        await _skip_items(request, response, index)
        await chat_repository.update_turn(
            request.session_id,
            request.turn_id,
            generation=response.epoch,
            status="partial" if index else "aborted",
            error=error[:500],
            finished_at=utc_now_naive(),
        )
    except Exception:
        logger.opt(exception=True).warning("[Chat] 记录发送作废状态时出错")


async def mark_send_failed(request: ChatRequest, response: ChatResponse, exc: Exception, index: int = 0) -> None:
    """发送失败时保留生成文本；已成功的前几段不回滚，轮次按是否发过区分。"""
    if not memory_state.is_ready() or not history_enabled(request.is_group):
        return
    try:
        turn_item_id = item_id(response, index)
        if turn_item_id is not None:
            await chat_repository.update_turn_item(
                request.session_id, turn_item_id, generation=response.epoch, send_status="failed", visible=False
            )
        await _skip_items(request, response, index + 1)
        await chat_repository.update_turn(
            request.session_id,
            request.turn_id,
            generation=response.epoch,
            status="partial" if index else "failed",
            error=f"发送失败: {type(exc).__name__}: {exc}"[:500],
            finished_at=utc_now_naive(),
        )
    except Exception:
        logger.opt(exception=True).warning("[Chat] 记录发送失败状态时出错")


async def mark_turn_aborted(request: ChatRequest, error: str = "") -> None:
    """清空或打断后作废在途轮次。"""
    if not memory_state.is_ready() or not history_enabled(request.is_group):
        return
    try:
        await chat_repository.update_turn(
            request.session_id,
            request.turn_id,
            generation=request.accepted_epoch,
            status="aborted",
            error=error[:500],
            finished_at=utc_now_naive(),
        )
    except Exception:
        logger.opt(exception=True).warning("[Chat] 记录轮次作废状态时出错")


async def mark_turn_failed(request: ChatRequest, exc: Exception) -> None:
    """记录本轮失败；轮次行不存在时什么也不做。"""
    if not memory_state.is_ready() or not history_enabled(request.is_group):
        return
    try:
        await chat_repository.update_turn(
            request.session_id,
            request.turn_id,
            generation=request.accepted_epoch,
            status="failed",
            error=f"{type(exc).__name__}: {exc}"[:500],
            finished_at=utc_now_naive(),
        )
    except Exception:
        logger.opt(exception=True).warning("[Chat] 记录轮次失败状态时出错")


async def record_group_message(
    service: Any,
    session_id: str,
    user_id: str,
    user_name: str,
    text: str,
    *,
    chat_cfg: dict[str, Any],
    limit: int,
    images: list[str | bytes] | None = None,
    has_image: bool = False,
    mentions: list[MentionRef] | None = None,
    dedupe_key: str = "",
    platform: str = "",
    event_id: str | None = None,
    message_id: str | None = None,
    reply_to_message_id: str | None = None,
    occurred_at: datetime | None = None,
    memory_images: list[tuple[str, str | bytes]] | None = None,
    has_at_bot: bool = False,
    sender_role: str | None = None,
    quote_snapshot: dict[str, str] | None = None,
) -> None:
    """记录群聊或私聊的上下文消息，图片同样转成文字描述写入历史。"""
    if limit <= 0:
        return
    images = list(images or [])
    placeholders = ["[图片]"] * len(images)
    if not placeholders and has_image:
        placeholders = ["[图片]"]
    label = speaker_label(user_id, user_name)
    lines = [f"{label}: {text}" if text else f"{label}:"]
    if line := mentions_line(mentions or []):
        lines.append(line)
    lines.extend(placeholders)
    entry: dict[str, Any] = {"role": "user", "content": "\n".join(lines)}
    context = message_metadata(
        timestamp=occurred_at or utc_now_naive(),
        sender_id=user_id,
        sender_name=user_name,
        quote_id=reply_to_message_id or "",
        at_bot=has_at_bot,
        sender_role=sender_role,
        quote_snapshot=quote_snapshot,
    )
    entry.update(context)
    if images:
        entry["_image_sources"] = images
    if message_id:
        # 只给运行时用的来源标记；撤回通知按平台消息 ID 找到并丢弃这一条
        entry["_source_ids"] = [str(message_id)]
    async with service.session_store.lock(session_id):
        service.session_store.append(session_id, entry, limit=limit)
    row_id: int | None = None
    if memory_state.is_ready():
        from ..amemorix.image_writeback import image_writeback

        memory_sources = [source for _, source in memory_images] if memory_images is not None else images
        memory_paths = [path for path, _ in memory_images] if memory_images is not None else None
        image_items = await image_writeback.prepare(session_id, memory_sources, memory_paths)
        payload = json.dumps(
            {"has_image": bool(has_image or images), "image_memory": image_items, "context": context},
            ensure_ascii=False,
        )
        try:
            row_id = await chat_repository.append_message(
                ChatMessage(
                    tenant=session_id,
                    kind="user",
                    platform=platform,
                    platform_event_id=event_id,
                    platform_message_id=message_id,
                    reply_to_message_id=context["_quote_ids"][0] if context["_quote_ids"] else None,
                    sender_id=user_id,
                    sender_name=user_name,
                    content=entry["content"],
                    payload=payload,
                    source="live",
                    dedupe_key=dedupe_key or f"uuid:{uuid4().hex}",
                    occurred_at=to_naive_utc(occurred_at or utc_now_naive()),
                )
            )
        except Exception:
            logger.opt(exception=True).warning("[Chat] 群聊消息落库失败，本轮仅用内存历史")
        if row_id is not None and image_items:
            await image_writeback.enqueue(session_id, message_id or dedupe_key)
    if row_id is not None:
        group_id = session_id.partition(":group:")[2] if ":group:" in session_id else ""
        await _track_message(session_id, platform, user_id, user_name, group_id, occurred_at)
    threshold = int((chat_cfg.get("message_receive") or {}).get("image_parse_threshold", 5))
    if images and vision_configured(chat_cfg) and len(images) <= threshold:
        try:
            async with fallback_provider("vision", session_id) as provider:
                descriptions = await describe_sources(provider, images)
        except Exception as exc:
            logger.opt(exception=True).debug("[Chat] 群聊图片描述跳过：Provider 不可用")
            logger.warning(f"[Chat] 群聊图片描述跳过：Provider 不可用（{type(exc).__name__}）")
        else:
            if any(descriptions):
                entry["_image_descriptions"] = list(descriptions)
            entry["content"] = "\n".join(
                lines[: -len(images)] + [f"[图片: {desc}]" if desc else "[图片]" for desc in descriptions]
            )
            if row_id is not None:
                try:
                    await chat_repository.update_message_content(session_id, row_id, entry["content"])
                except Exception:
                    logger.opt(exception=True).warning("[Chat] 图片描述落库失败")


async def record_trace(
    request: ChatRequest,
    provider_name: str,
    latency_ms: int,
    error: str,
    trace: dict[str, Any],
) -> None:
    """写入轮次追踪；失败不影响对话。"""
    if not memory_state.is_ready() or not history_enabled(request.is_group):
        return
    budget = dict(trace.get("budget") or {})
    if trace.get("plan_action"):
        budget["plan_action"] = trace["plan_action"]
    try:
        await chat_repository.save_trace(
            request.session_id,
            turn_id=request.turn_id,
            trigger_kind=str(request.trigger_kind),
            request_preview=str(trace.get("request_preview", "")),
            injected=json.dumps(trace.get("injected", {}), ensure_ascii=False),
            budget=json.dumps(budget, ensure_ascii=False),
            provider=provider_name,
            latency_ms=latency_ms,
            error=error,
        )
    except Exception:
        logger.opt(exception=True).debug("[Chat] 写入 trace 失败")


async def end_turn_without_reply(request: ChatRequest, provider_name: str) -> None:
    """规划为不回复时把轮次标成 ended，保留用户消息作为上下文。"""
    if not memory_state.is_ready() or not history_enabled(request.is_group):
        return
    try:
        await chat_repository.update_turn(
            request.session_id,
            request.turn_id,
            generation=request.accepted_epoch,
            status="ended",
            provider=provider_name,
            finished_at=utc_now_naive(),
        )
    except Exception:
        logger.opt(exception=True).warning("[ChatPlanner] 记录结束轮次失败")


async def clear_tenant(session_id: str) -> int:
    """清空数据库历史并返回新的持久化代次。"""
    return await chat_repository.clear_tenant(session_id)
