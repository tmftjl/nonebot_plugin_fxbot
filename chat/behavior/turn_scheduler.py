"""适配 MaiBot 062d45c MessageTurnScheduler 的会话消息调度。"""

from __future__ import annotations

import time
import asyncio
from typing import Any
from datetime import datetime
from dataclasses import field, replace, dataclass

from nonebot import logger

from ..types import ChatRequest
from .debounce import PendingBatch, DebounceRegistry, default_debounce_registry
from ...utils.tz import as_utc_aware
from .reply_gate import talk_value_for, proactive_switch, default_reply_gate_registry
from ..work_schedule import can_reply

MAX_SCHEDULED_MESSAGES = 100
PENDING_TTL_SECONDS = 300.0


def _received_at(request: ChatRequest) -> float:
    """读取适配前的接收时刻，缺失时按进入调度器的时间处理。"""
    value = request.metadata.get("received_at")
    if isinstance(value, str):
        try:
            return as_utc_aware(datetime.fromisoformat(value)).timestamp()
        except (ValueError, OverflowError, OSError):
            pass
    return time.time()


@dataclass
class ScheduledChat:
    """一个会话的待处理消息、防抖状态和唯一处理任务。"""

    service: Any
    runtime: Any
    pending: list[ChatRequest] = field(default_factory=list)
    received_at: dict[str, float] = field(default_factory=dict)
    quiet: PendingBatch | None = None
    event: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task | None = None
    sequence: int = 0


class NormalChatScheduler:
    """先防抖，再静默、强制触发、退避和动态门控，所有状态按会话保存。"""

    def __init__(self) -> None:
        self.chats: dict[str, ScheduledChat] = {}
        self.debounce = DebounceRegistry()
        self.stopping = False

    async def submit(self, service: Any, request: ChatRequest, runtime: Any) -> None:
        """保存消息后唤醒会话 worker，进行中的 Planner 直接接收后续消息。"""
        if self.stopping or not can_reply(session_id=request.session_id):
            return
        received_at = _received_at(request)
        if request.is_group:
            default_reply_gate_registry.get(request.session_id).record_message(request.dedupe_key, now=received_at)
        if await service.queue_message(request, runtime):
            if request.is_group and request.trigger_kind in {"mention", "persona"}:
                default_reply_gate_registry.get(request.session_id).record_forced_turn([request.dedupe_key])
            return
        state = self.chats.get(request.session_id)
        if state is None:
            state = ScheduledChat(service=service, runtime=runtime)
            self.chats[request.session_id] = state
        state.runtime = runtime
        state.pending.append(request)
        state.received_at[request.dedupe_key] = received_at
        self._trim_pending(state)
        state.sequence += 1
        if state.quiet is None:
            state.quiet = self.debounce.create(request)
        else:
            if state.quiet.full:
                state.quiet.requests = state.quiet.requests[-1:]
            state.quiet.accept(request, trigger=request.trigger_kind != "proactive")
        state.event.set()
        if state.task is None or state.task.done():
            state.task = asyncio.create_task(self._run(request.session_id, state))

    def _trim_pending(self, state: ScheduledChat) -> None:
        """只保留有效期内最近一批待回复消息，不影响已写入的聊天历史。"""
        cutoff = time.time() - PENDING_TTL_SECONDS
        state.pending[:] = [request for request in state.pending if state.received_at[request.dedupe_key] > cutoff][
            -MAX_SCHEDULED_MESSAGES:
        ]
        state.received_at = {request.dedupe_key: state.received_at[request.dedupe_key] for request in state.pending}

    async def _wait(self, state: ScheduledChat, sequence: int, seconds: float | None = None) -> None:
        """等待新消息或退避到期，不丢失判断期间已经到达的唤醒。"""
        state.event.clear()
        if state.sequence != sequence:
            return
        try:
            if seconds is None:
                await state.event.wait()
            else:
                await asyncio.wait_for(state.event.wait(), seconds)
        except asyncio.TimeoutError:
            pass

    async def _run(self, tenant: str, state: ScheduledChat) -> None:
        """一个会话只维护一条调度循环和一条生成发送链。"""
        try:
            while not self.stopping and self.chats.get(tenant) is state:
                if not can_reply(session_id=tenant):
                    return
                if state.quiet is not None:
                    quiet = state.quiet
                    await quiet.wait()
                    if quiet.cancelled:
                        return
                    self.debounce.pop(quiet)
                    state.quiet = None
                self._trim_pending(state)
                if not state.pending:
                    default_reply_gate_registry.get(tenant).reset_pending()
                    return
                sequence = state.sequence
                snapshot = list(state.pending)
                latest = snapshot[-1]
                if latest.accepted_epoch is not None and not state.service.is_current(tenant, latest.accepted_epoch):
                    return
                forced = any(request.trigger_kind in {"mention", "persona"} for request in snapshot)
                gate = default_reply_gate_registry.get(tenant)
                if latest.is_group and talk_value_for(tenant) <= 0:
                    state.pending.clear()
                    return
                if latest.is_group and not forced:
                    if await proactive_switch(tenant) is False:
                        state.pending.clear()
                        return
                    if self.chats.get(tenant) is not state or self.stopping:
                        return
                    if state.sequence != sequence:
                        continue
                    delay = gate.backoff_delay(pending_count=len(snapshot))
                    if delay > 0:
                        expires_in = min(state.received_at.values()) + PENDING_TTL_SECONDS - time.time()
                        await self._wait(state, sequence, min(delay, max(0.0, expires_in)))
                        continue
                    observation = gate.observe_message(
                        [request.dedupe_key for request in snapshot],
                        [request.text or ("[图片]" if request.images else "") for request in snapshot],
                        has_at_bot=any(request.metadata.get("has_at_bot", False) for request in snapshot),
                        at_other=any(request.metadata.get("at_other", False) for request in snapshot),
                    )
                    if not observation.would_trigger:
                        expires_in = min(state.received_at.values()) + PENDING_TTL_SECONDS - time.time()
                        await self._wait(state, sequence, max(0.0, expires_in))
                        continue
                    gate.mark_triggered()
                elif latest.is_group:
                    gate.record_forced_turn([request.dedupe_key for request in snapshot])
                merged = replace(latest, metadata=dict(latest.metadata))
                if len(snapshot) > 1:
                    batch = PendingBatch(
                        tenant=tenant,
                        owner_id=latest.user_id,
                        requests=snapshot,
                        started=0,
                        deadline=0,
                        forced_kind=next(
                            (item.trigger_kind for item in reversed(snapshot) if item.trigger_kind != "proactive"), ""
                        ),
                        has_proactive=any(item.trigger_kind == "proactive" for item in snapshot),
                        accepted_epoch=latest.accepted_epoch,
                    )
                    merged = batch.merge()
                    merged.metadata = dict(merged.metadata)
                merged.metadata["history_recorded"] = True
                merged.metadata["history_appended"] = True
                merged.metadata["source_senders"] = {
                    request.message_id or request.dedupe_key: {
                        "user_id": request.user_id,
                        "user_name": request.user_name,
                        "text": request.text,
                        "role": request.metadata.get("sender_role"),
                        "quote_snapshot": request.metadata.get("quote_snapshot"),
                    }
                    for request in snapshot
                }
                del state.pending[: len(snapshot)]
                for request in snapshot:
                    state.received_at.pop(request.dedupe_key, None)
                runtime = state.runtime
                try:
                    await state.service.process_and_send(merged, runtime)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.opt(exception=True).warning("[ChatScheduler] 会话规划或发送失败")
        finally:
            if self.chats.get(tenant) is state:
                self.chats.pop(tenant, None)
                self.debounce.clear(tenant)

    def clear(self, tenant: str) -> None:
        """清空时取消待处理任务和防抖，避免旧消息重新触发。"""
        state = self.chats.pop(tenant, None)
        if state is not None and state.task is not None:
            state.task.cancel()
        self.debounce.clear(tenant)

    async def stop(self) -> None:
        """关闭时先取消全部会话 worker，再释放模型资源。"""
        self.stopping = True
        tasks = [state.task for state in self.chats.values() if state.task is not None]
        for tenant in list(self.chats):
            self.clear(tenant)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


normal_scheduler = NormalChatScheduler()
normal_scheduler.debounce = default_debounce_registry
