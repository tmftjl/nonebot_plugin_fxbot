"""同一会话的触发消息防抖缓冲。

等待窗口参考 MaiBot `src/chat/debounce/controller.py`：安静/活跃开批、
同人续发、@ 加速。FxBot 只保留 P0.5-1 需要的窗口顺延与整批上限。
"""

from __future__ import annotations

import time
import asyncio
from typing import Any
from dataclasses import field, replace, dataclass

from ..types import ChatRequest, speaker_label

GROUP_WINDOW = 2.0
PRIVATE_WINDOW = 0.8
BATCH_MAX_MESSAGES = 10
BATCH_MAX_SECONDS = 30.0


def window_for(request: ChatRequest) -> float:
    """群聊 2 秒、私聊 0.8 秒。"""
    return GROUP_WINDOW if request.is_group else PRIVATE_WINDOW


def _now() -> float:
    """单调时钟；测试可替换以验证窗口顺延与封顶。"""
    return time.monotonic()


@dataclass
class PendingBatch:
    """一个 tenant 内等待合并的一批消息。"""

    tenant: str
    owner_id: str
    requests: list[ChatRequest]
    started: float
    deadline: float
    trigger_seq: int = 0
    accepted_epoch: int | None = None
    forced: bool = False
    cancelled: bool = False
    changed: asyncio.Event = field(default_factory=asyncio.Event)

    @property
    def full(self) -> bool:
        """是否已达到单批消息上限。"""
        return len(self.requests) >= BATCH_MAX_MESSAGES

    def accept(self, request: ChatRequest, *, trigger: bool) -> bool:
        """并入一条消息并顺延窗口；已满时拒绝。"""
        if self.full:
            return False
        self.requests.append(request)
        if trigger:
            self.forced = True
            self.trigger_seq = request.trigger_seq or self.trigger_seq
            if request.accepted_epoch is not None:
                self.accepted_epoch = request.accepted_epoch
        self.deadline = min(_now() + window_for(request), self.started + BATCH_MAX_SECONDS)
        self.changed.set()
        return True

    async def wait(self) -> None:
        """等到窗口结束；期间的新消息会顺延，最多到整批上限。"""
        while not self.cancelled:
            remaining = self.deadline - _now()
            if remaining <= 0:
                return
            self.changed.clear()
            try:
                await asyncio.wait_for(self.changed.wait(), remaining)
            except asyncio.TimeoutError:
                return

    def merge(self) -> ChatRequest:
        """合并成一个请求；同作者续发不重复加发言人前缀。"""
        if len(self.requests) == 1:
            return self.requests[0]
        first = self.requests[0]
        texts: list[str] = []
        images: list[Any] = []
        mentions: list[Any] = []
        message_ids: list[str] = []
        author_ids: list[str] = []
        quoted = ""
        for index, request in enumerate(self.requests):
            text = request.text.strip()
            if text:
                if request.user_id == first.user_id:
                    texts.append(text)
                else:
                    texts.append(f"{speaker_label(request.user_id, request.user_name)}: {text}")
            if not quoted:
                quoted = request.quoted_text
            for image in request.images:
                if image not in images:
                    images.append(image)
            known = {ref.user_id for ref in mentions}
            mentions.extend(ref for ref in request.mentions if ref.user_id not in known)
            message_ids.append(request.message_id or request.dedupe_key)
            if request.user_id not in author_ids:
                author_ids.append(request.user_id)
        return replace(
            first,
            text="\n".join(texts),
            images=images,
            mentions=mentions,
            quoted_text=quoted,
            batched=True,
            source_message_ids=message_ids,
            source_author_ids=author_ids,
            trigger_seq=self.trigger_seq,
            accepted_epoch=self.accepted_epoch,
            activity_seq=max(request.activity_seq for request in self.requests),
        )


class DebounceRegistry:
    """按 tenant 保存批次；同一时刻只允许首个触发消息负责 flush。"""

    def __init__(self) -> None:
        self._batches: dict[str, PendingBatch] = {}

    def get(self, tenant: str) -> PendingBatch | None:
        """读取未完成的批次。"""
        return self._batches.get(tenant)

    def create(self, request: ChatRequest) -> PendingBatch:
        """首个触发消息创建批次。"""
        now = _now()
        batch = PendingBatch(
            tenant=request.session_id,
            owner_id=request.user_id,
            requests=[request],
            started=now,
            deadline=now + window_for(request),
            trigger_seq=request.trigger_seq,
            accepted_epoch=request.accepted_epoch,
            forced=True,
        )
        self._batches[request.session_id] = batch
        return batch

    def merge_trigger(self, request: ChatRequest) -> PendingBatch | None:
        """把新的 @/人格触发并入已有批次；没有批次或已满时返回 None。"""
        batch = self._batches.get(request.session_id)
        if batch is None or not batch.accept(request, trigger=True):
            return None
        return batch

    def merge_context(self, request: ChatRequest) -> bool:
        """同作者的普通群消息并入批次，其他作者仍立即写历史。"""
        batch = self._batches.get(request.session_id)
        if batch is None or batch.owner_id != request.user_id:
            return False
        return batch.accept(request, trigger=False)

    def pop(self, batch: PendingBatch) -> None:
        """负责 flush 的处理器收走批次。"""
        if self._batches.get(batch.tenant) is batch:
            self._batches.pop(batch.tenant, None)

    def clear(self, tenant: str) -> None:
        """清空会话时取消待合并批次，避免清空后仍发送旧回复。"""
        batch = self._batches.pop(tenant, None)
        if batch is not None:
            batch.cancelled = True
            batch.changed.set()


default_debounce_registry = DebounceRegistry()
