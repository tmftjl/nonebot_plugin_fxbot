"""同一会话的触发消息防抖缓冲。

等待窗口移植 MaiBot `src/chat/debounce/controller.py`（commit 062d45c）：安静/活跃开批、
同人/换人续发、裸图下限、@ 与提及加速、整批上限。
"""

from __future__ import annotations

import time
import asyncio
from typing import Any
from dataclasses import field, replace, dataclass

from ..types import ChatRequest, speaker_label
from ...config import get_manager as get_config_manager

FORCED_FLUSH_SECONDS = 0.5
BATCH_MAX_MESSAGES = 10
BATCH_LIMIT = 8.0
QUIET_THRESHOLD = 30.0
BARE_IMAGE_MIN_WAIT = 4.0
MENTION_LIMIT = 1.0
BATCH_MAX_SECONDS = BATCH_LIMIT


@dataclass(frozen=True)
class WaitTimes:
    """一档防抖的四类等待秒数。"""

    quiet: float
    active: float
    same_author: float
    different_author: float


WAIT_PROFILES = {
    "fast": WaitTimes(1.0, 3.0, 1.0, 0.5),
    "natural": WaitTimes(2.0, 5.0, 2.0, 0.5),
    "steady": WaitTimes(4.0, 7.0, 4.0, 2.0),
}
MODES = frozenset({"off", "fast", "natural", "steady"})


def _now() -> float:
    """单调时钟；测试可替换以验证窗口顺延与封顶。"""
    return time.monotonic()


def _mode() -> str:
    """读取防抖档位；配置缺失或非法时按自然档。"""
    try:
        value = get_config_manager().get_system()["chat"]["reply_timing"]["message_debounce_mode"]
    except Exception:
        return "natural"
    return value if value in MODES else "natural"


def _urgent_at(request: ChatRequest, now: float) -> float | None:
    """@ 或提及后的 1 秒上限从消息到达起算。"""
    return now + MENTION_LIMIT if request.trigger_kind in {"mention", "persona"} else None


def _waits(mode: str) -> WaitTimes:
    return WAIT_PROFILES.get(mode, WAIT_PROFILES["natural"])


def quiet_wait(same_author: bool | None) -> float:
    """Focus 入站判断前的安静窗口；None 表示该聊天本轮首条消息；off 档立即就绪。"""
    mode = _mode()
    if mode == "off":
        return 0.0
    waits = _waits(mode)
    if same_author is None:
        return waits.quiet
    return waits.same_author if same_author else waits.different_author


@dataclass
class PendingBatch:
    """一个 tenant 内等待合并的一批消息。"""

    tenant: str
    owner_id: str
    requests: list[ChatRequest]
    started: float
    deadline: float
    trigger_seq: int = 0
    trigger_owner: str = ""
    accepted_epoch: int | None = None
    forced: bool = False
    forced_kind: str = ""
    has_proactive: bool = False
    cancelled: bool = False
    last_author: str = ""
    last_arrival: float = 0.0
    urgent: float | None = None
    changed: asyncio.Event = field(default_factory=asyncio.Event)

    @property
    def full(self) -> bool:
        """是否已达到单批消息上限。"""
        return len(self.requests) >= BATCH_MAX_MESSAGES

    def accept(self, request: ChatRequest, *, trigger: bool, extend: bool = True) -> bool:
        """并入一条消息并按需顺延窗口；已满时拒绝。"""
        if self.full:
            return False
        self.requests.append(request)
        self.has_proactive = self.has_proactive or request.proactive or request.trigger_kind == "proactive"
        if trigger:
            self.forced = True
            if request.trigger_kind in {"mention", "persona", "private"}:
                self.forced_kind = request.trigger_kind
            self.trigger_seq = request.trigger_seq or self.trigger_seq
            if request.trigger_seq:
                # 合并后的轮次按最新触发者的序号判断是否被取代
                self.trigger_owner = request.trigger_owner or request.user_id
            if request.accepted_epoch is not None:
                self.accepted_epoch = request.accepted_epoch
        now = _now()
        if extend:
            if trigger and (urgent := _urgent_at(request, now)):
                self.urgent = min(self.urgent, urgent) if self.urgent is not None else urgent
            mode = _mode()
            if mode == "off":
                self.deadline = now
            else:
                waits = _waits(mode)
                if trigger and request.trigger_kind in {"mention", "persona"} and self.has_proactive:
                    candidate = now + FORCED_FLUSH_SECONDS
                else:
                    wait = waits.same_author if request.user_id == self.last_author else waits.different_author
                    candidate = now + wait
                limit = self.started + BATCH_LIMIT
                if self.urgent is not None:
                    limit = min(limit, self.urgent)
                self.deadline = min(candidate, limit)
        self.last_author = request.user_id
        self.last_arrival = now
        self.changed.set()
        return True

    async def wait(self) -> None:
        """等到窗口结束；期间的新消息会顺延，配置改为关闭时立即结束。"""
        while not self.cancelled:
            remaining = self.deadline - _now() if _mode() != "off" else 0.0
            if remaining <= 0:
                return
            self.changed.clear()
            try:
                await asyncio.wait_for(self.changed.wait(), min(remaining, 0.5))
            except asyncio.TimeoutError:
                pass

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
            trigger_kind=self.forced_kind or first.trigger_kind,
            # 批次里出现强制触发时按普通轮处理，只有纯主动批次才算主动轮
            proactive=self.has_proactive and not self.forced_kind,
            trigger_seq=self.trigger_seq,
            trigger_owner=self.trigger_owner or first.trigger_owner or first.user_id,
            accepted_epoch=self.accepted_epoch,
            activity_seq=max(request.activity_seq for request in self.requests),
        )


class DebounceRegistry:
    """按 tenant 保存批次；同一时刻只允许首个触发消息负责 flush。"""

    def __init__(self) -> None:
        self._batches: dict[str, PendingBatch] = {}
        self._history: dict[str, tuple[float, str]] = {}

    def get(self, tenant: str) -> PendingBatch | None:
        """读取未完成的批次。"""
        return self._batches.get(tenant)

    def create(self, request: ChatRequest) -> PendingBatch:
        """首个触发消息创建批次。"""
        now = _now()
        previous = self._history.get(request.session_id)
        mode = _mode()
        urgent = _urgent_at(request, now)
        if mode == "off":
            deadline = now
        else:
            waits = _waits(mode)
            active = previous is not None and now - previous[0] <= QUIET_THRESHOLD
            wait = waits.active if active else waits.quiet
            if request.images and not request.text.strip():
                wait = max(wait, BARE_IMAGE_MIN_WAIT)
            limit = now + BATCH_LIMIT
            if urgent is not None:
                limit = min(limit, urgent)
            deadline = min(now + wait, limit)
        batch = PendingBatch(
            tenant=request.session_id,
            owner_id=request.user_id,
            requests=[request],
            started=now,
            deadline=deadline,
            trigger_seq=request.trigger_seq,
            trigger_owner=request.trigger_owner or request.user_id,
            accepted_epoch=request.accepted_epoch,
            forced=True,
            forced_kind=request.trigger_kind if request.trigger_kind != "proactive" else "",
            has_proactive=request.proactive or request.trigger_kind == "proactive",
            last_author=request.user_id,
            last_arrival=now,
            urgent=urgent,
        )
        self._batches[request.session_id] = batch
        return batch

    def merge_trigger(self, request: ChatRequest) -> PendingBatch | None:
        """把新的 @/人格触发并入已有批次；没有批次或已满时返回 None。"""
        batch = self._batches.get(request.session_id)
        if batch is None or not batch.accept(request, trigger=True):
            return None
        return batch

    def merge_proactive(self, request: ChatRequest) -> PendingBatch | None:
        """主动轮并入已有批次但不延长等待，没有批次或已满时返回 None。"""
        batch = self._batches.get(request.session_id)
        if batch is None or not batch.accept(request, trigger=False, extend=False):
            return None
        return batch

    def merge_context(self, request: ChatRequest) -> bool:
        """同一会话的普通消息并入批次，保留各自发言人。"""
        batch = self._batches.get(request.session_id)
        if batch is None:
            return False
        return batch.accept(request, trigger=False)

    def pop(self, batch: PendingBatch) -> None:
        """负责 flush 的处理器收走批次。"""
        if self._batches.get(batch.tenant) is batch:
            self._batches.pop(batch.tenant, None)
            self._history[batch.tenant] = (batch.last_arrival, batch.last_author)

    def discard_message(self, tenant: str, message_id: str) -> bool:
        """撤回消息：把它从待合并批次里去掉；撤回的是触发消息时整批取消。"""
        batch = self._batches.get(tenant)
        if batch is None:
            return False
        key = str(message_id)
        removed = [request for request in batch.requests if request.message_id == key]
        if not removed:
            return False
        # 触发消息是开批的那条或被并入的新触发：它的撤回让整批失去发言理由
        trigger_removed = any(
            request is batch.requests[0] or (request.trigger_seq and request.trigger_seq == batch.trigger_seq)
            for request in removed
        )
        remaining = [request for request in batch.requests if not any(request is target for target in removed)]
        if trigger_removed or not remaining:
            self.clear(tenant)
            return True
        batch.requests = remaining
        batch.has_proactive = any(
            request.proactive or request.trigger_kind == "proactive" for request in batch.requests
        )
        return True

    def clear(self, tenant: str) -> None:
        """清空会话时取消待合并批次，避免清空后仍发送旧回复。"""
        batch = self._batches.pop(tenant, None)
        self._history.pop(tenant, None)
        if batch is not None:
            batch.cancelled = True
            batch.changed.set()


default_debounce_registry = DebounceRegistry()
