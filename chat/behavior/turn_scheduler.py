"""适配 MaiBot 062d45c MessageTurnScheduler 的会话消息调度。"""

from __future__ import annotations

import asyncio
from typing import Any
from dataclasses import field, replace, dataclass

from nonebot import logger

from ..types import ChatRequest
from .debounce import PendingBatch, DebounceRegistry, default_debounce_registry
from .reply_gate import talk_value_for, proactive_switch, default_reply_gate_registry


@dataclass
class ScheduledChat:
    """一个会话的待处理消息、防抖状态和唯一处理任务。"""

    service: Any
    runtime: Any
    pending: list[ChatRequest] = field(default_factory=list)
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
        if self.stopping:
            return
        if await service.queue_message(request):
            return
        state = self.chats.get(request.session_id)
        if state is None:
            state = ScheduledChat(service=service, runtime=runtime)
            self.chats[request.session_id] = state
        state.runtime = runtime
        state.pending.append(request)
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
                if state.quiet is not None:
                    quiet = state.quiet
                    await quiet.wait()
                    if quiet.cancelled:
                        return
                    self.debounce.pop(quiet)
                    state.quiet = None
                if not state.pending:
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
                        await self._wait(state, sequence, delay)
                        continue
                    observation = gate.observe_message(
                        [request.dedupe_key for request in snapshot],
                        [request.text or ("[图片]" if request.images else "") for request in snapshot],
                        has_at_bot=any(request.metadata.get("has_at_bot", False) for request in snapshot),
                        at_other=any(request.metadata.get("at_other", False) for request in snapshot),
                    )
                    if not observation.would_trigger:
                        await self._wait(state, sequence)
                        continue
                    gate.mark_triggered()
                elif latest.is_group:
                    gate.record_forced_turn([request.dedupe_key for request in snapshot])
                merged = replace(snapshot[0], metadata=dict(snapshot[0].metadata))
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
                    }
                    for request in snapshot
                }
                del state.pending[: len(snapshot)]
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
