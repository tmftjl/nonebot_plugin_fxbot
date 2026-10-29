"""每个 Focus 共享组唯一的共享规划器；各聊天保留自己的历史、人格和发送目标。"""

from __future__ import annotations

import json
import time
import asyncio
from html import escape
from uuid import uuid4
from typing import Any
from functools import partial
from collections import OrderedDict
from dataclasses import field, replace, dataclass
from collections.abc import Callable

from nonebot import logger

from .tools import WAIT_TOOL, FocusToolRouter, _failure
from ..tools import ToolRuntime
from ..types import ChatRequest
from .context import FocusEntry, FocusCheckpointStore, restore_entries
from ...config import get_manager as get_config_manager
from ..service import chat_service, _memory_block
from ..memory.models import ChatPlannerSample
from ..replyer.prompt import recall_query, strip_images, build_persona_text, build_user_content
from ..context_summary import SUMMARY_HEADER, compact_messages
from ..behavior.debounce import quiet_wait
from ..memory.repository import chat_repository
from ..providers.routing import fallback_provider

MAX_SEEN_MESSAGES = 20000
MAX_INTERNAL_ROUNDS = 3
MAX_FAILURE_BACKOFF = 30
WAIT_REMINDER_AFTER = 3
_WAIT_REMINDER = "已经连续等待多次，为避免无限循环，请考虑延长等待或调用 stop 结束本轮。"
_FOCUS_PROMPT = (
    "你正在 Focus 共享模式下同时关注多个聊天，由你统一决定下一次发言。\n"
    "上面的消息来自不同聊天，每条开头标了 session_id。\n"
    "需要发言时调用 reply，并在 session_id 里填目标聊天；不要把回复正文写进普通输出。\n"
    "其他工具也必须填写 session_id，表示在哪个聊天里执行。\n"
    "需要继续观察时调用 wait；这一轮没有要做的事就调用 stop。\n"
    '各聊天的记忆与人格以 <focus_reference session_id="..."> 的形式提供，只用于判断怎么回复。'
)


@dataclass
class FocusMember:
    """共享组里一个聊天的最新接入信息。"""

    session_id: str
    bot: Any
    event: Any
    target: dict[str, Any]
    request: ChatRequest
    is_group: bool
    allowed_names: set[str] = field(default_factory=set)

    @property
    def runtime(self) -> ToolRuntime:
        """该聊天的工具运行时；发送目标由调用方注入。"""
        return ToolRuntime(bot=self.bot, event=self.event)


class FocusMatcher:
    """把发送目标包装成 matcher 形状，复用普通发送流程。"""

    def __init__(self, bot: Any, target: dict[str, Any], *, is_valid: Callable[[], bool] | None = None) -> None:
        self.bot = bot
        self.target = target
        self.is_valid = is_valid

    async def send(self, payload: Any) -> Any:
        from ...adapter import build_message, build_message_segment, send_message_to_target

        if isinstance(payload, str):
            payload = build_message(self.bot, build_message_segment(self.bot, "text", payload))
        if self.is_valid is not None and not self.is_valid():
            raise RuntimeError("Focus member is no longer valid")
        return await send_message_to_target(self.bot, self.target, payload)

    async def finish(self, *args: Any, **kwargs: Any) -> None:
        """Focus 没有需要收尾的 matcher。"""
        return None


class FocusGroupRuntime:
    """一个共享组的共享时间线、等待状态与共享规划循环。"""

    def __init__(self, scope_id: str, *, store: FocusCheckpointStore | None = None) -> None:
        self.scope_id = scope_id
        self.members: dict[str, FocusMember] = {}
        self.entries: list[FocusEntry] = []
        self._pending: dict[str, list[FocusEntry]] = {}
        self._ready: set[str] = set()
        self._debounce_tasks: dict[str, asyncio.Task[None]] = {}
        self._last_author: dict[str, str] = {}
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._considered: dict[str, int] = {}
        self._sequence = 0
        self._event = asyncio.Event()
        self._worker: asyncio.Task[None] | None = None
        self._closed = False
        self._failures = 0
        self._wait_until: float | None = None
        self._wait_session_id: str | None = None
        self._consecutive_waits = 0
        self._sent: list[FocusEntry] = []
        self._invalidated = False
        self._store = store or FocusCheckpointStore(scope_id)
        self._checkpoint_lock = asyncio.Lock()
        self.tools = FocusToolRouter(self)

    async def restore(self) -> None:
        """恢复上次保存的共享上下文；不重放旧工具调用。"""
        payload = await asyncio.to_thread(self._store.load)
        if not payload:
            return
        self.entries = restore_entries(payload)
        self._sequence = int(payload.get("sequence") or 0)
        self._considered = {str(key): int(value) for key, value in (payload.get("considered") or {}).items()}
        self._seen = OrderedDict((str(key), None) for key in payload.get("seen") or [])
        self._wait_until = payload.get("wait_until")
        self._wait_session_id = payload.get("wait_session_id")
        self._consecutive_waits = int(payload.get("consecutive_waits") or 0)
        if self._wait_session_id is None or not any(
            self._wait_session_id in entry.session_ids for entry in self.entries
        ):
            self._wait_until = self._wait_session_id = None

    async def checkpoint(self) -> None:
        """把时间线与等待状态落盘。"""
        async with self._checkpoint_lock:
            pending = sorted((entry for batch in self._pending.values() for entry in batch), key=lambda e: e.sequence)
            entries = [*self.entries, *pending, *self._sent]
            state = {
                "sequence": self._sequence,
                "considered": dict(self._considered),
                "seen": list(self._seen),
                "wait_until": self._wait_until,
                "wait_session_id": self._wait_session_id,
                "consecutive_waits": self._consecutive_waits,
            }
            await asyncio.to_thread(self._store.save, entries, state)

    def add_member(self, member: FocusMember) -> None:
        """接入或刷新一个聊天；已有未处理输入时立即安排思考。"""
        self.members[member.session_id] = member
        if self._wait_until is not None and time.time() >= self._wait_until:
            self._wait_until = self._wait_session_id = None
        if self._has_unconsidered(member.session_id) or self._pending.get(member.session_id):
            self._ready.add(member.session_id)
        if member.session_id in self._ready or self._wait_until is not None:
            self._event.set()
            self._ensure_worker()

    def is_member_valid(self, member: FocusMember) -> bool:
        """成员仍属于当前共享组且 Focus 仍开启。"""
        from .manager import focus_mode_manager

        return (
            self.members.get(member.session_id) is member
            and not self._closed
            and focus_mode_manager.is_enabled_for_session(member.session_id)
            and focus_mode_manager.scope_key(member.session_id) == self.scope_id
        )

    async def remove_member(self, session_id: str, *, clear_context: bool) -> None:
        """移出一个聊天；清空上下文时连同涉及它的原子段一起丢弃。"""
        task = self._debounce_tasks.pop(session_id, None)
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if self._wait_session_id == session_id:
            self._finish_wait("发起等待的聊天已退出共享组")
        member = self.members.pop(session_id, None)
        if member is None:
            return
        pending = self._pending.pop(session_id, [])
        if not clear_context:
            self.entries.extend(pending)
            self.entries.extend(entry for entry in self._sent if session_id in entry.session_ids)
        self._ready.discard(session_id)
        self._last_author.pop(session_id, None)
        self._sent = [entry for entry in self._sent if session_id not in entry.session_ids]
        if clear_context:
            self.entries = [entry for entry in self.entries if session_id not in entry.session_ids]
            self._considered.pop(session_id, None)
            self._seen = OrderedDict((key, None) for key in self._seen if not key.startswith(session_id + "\0"))
        await self.checkpoint()
        if self.members:
            self._ensure_worker()
            self._event.set()

    async def discard_message(self, session_id: str, message_id: str) -> bool:
        """撤回消息：从待处理输入与共享时间线里去掉它，并让在途生成重来。"""
        key = str(message_id)
        removed = False
        pending = self._pending.get(session_id)
        if pending:
            kept = [entry for entry in pending if key not in entry.sources.get(session_id, ())]
            if len(kept) != len(pending):
                self._pending[session_id] = kept
                removed = True
        kept_entries = [entry for entry in self.entries if key not in entry.sources.get(session_id, ())]
        if len(kept_entries) != len(self.entries):
            self.entries = kept_entries
            removed = True
        if removed:
            self._invalidated = True
            await self.checkpoint()
        return removed

    async def purge_session(self, session_id: str) -> None:
        """丢弃某个聊天留在共享时间线上的全部痕迹。"""
        self.entries = [entry for entry in self.entries if session_id not in entry.session_ids]
        self._pending.pop(session_id, None)
        self._ready.discard(session_id)
        self._considered.pop(session_id, None)
        self._seen = OrderedDict((key, None) for key in self._seen if not key.startswith(session_id + "\0"))
        await self.checkpoint()

    async def register(self, member: FocusMember, request: ChatRequest, *, is_at: bool) -> None:
        """登记一条入站消息；@ 立即就绪，其余等安静窗口。"""
        key = request.message_id or request.dedupe_key
        if not self._remember(member.session_id, key):
            return
        self._queue_input(member.session_id, request, is_at)
        if is_at and self._wait_until is not None:
            self._finish_wait("等待期间收到 @")
            self._ready.update(self._pending)
        elif is_at:
            self._ready.add(member.session_id)
            self._event.set()
        else:
            self._schedule_debounce(member.session_id, request.user_id)
        await self.checkpoint()
        self._ensure_worker()

    def _remember(self, session_id: str, message_id: str) -> bool:
        key = session_id + "\0" + message_id
        if key in self._seen:
            return False
        self._seen[key] = None
        while len(self._seen) > MAX_SEEN_MESSAGES:
            self._seen.popitem(last=False)
        return True

    def _queue_input(self, session_id: str, request: ChatRequest, is_at: bool) -> None:
        self._sequence += 1
        content = strip_images([{"role": "user", "content": build_user_content(request)}])[0]["content"]
        key = request.message_id or request.dedupe_key
        entry = FocusEntry(
            [{"role": "user", "content": f"[{session_id}]\n{content}"}],
            {session_id},
            self._sequence,
            {session_id: [[self._sequence, key]]},
            is_at,
            sources={session_id: [key]},
        )
        self._pending.setdefault(session_id, []).append(entry)

    def _schedule_debounce(self, session_id: str, author: str) -> None:
        known = self._last_author.get(session_id)
        self._last_author[session_id] = author
        task = self._debounce_tasks.get(session_id)
        if task is not None and not task.done():
            task.cancel()
        self._debounce_tasks[session_id] = asyncio.create_task(
            self._debounce(session_id, quiet_wait(None if known is None else known == author))
        )

    async def _debounce(self, session_id: str, seconds: float) -> None:
        try:
            await asyncio.sleep(seconds)
        except asyncio.CancelledError:
            return
        if session_id in self.members:
            self._ready.add(session_id)
            self._event.set()

    def control_wait(self, arguments: dict[str, Any], session_id: str) -> tuple[str, bool]:
        """整组暂停；等待期间任何聊天收到 @ 都会提前唤醒。"""
        seconds = arguments.get("seconds")
        if isinstance(seconds, bool) or not isinstance(seconds, int) or seconds < 0:
            return _failure("seconds 必须是非负整数。"), False
        self._consecutive_waits += 1
        self._wait_until = time.time() + seconds
        self._wait_session_id = session_id or next(iter(self.members), "")
        if any(entry.is_at for batch in self._pending.values() for entry in batch):
            self._finish_wait("等待登记前已有尚未提交的 @ 消息")
            self._ready.update(self._pending)
            result = "已登记整组等待，但已有尚未提交的 @ 消息，立即继续思考。"
        else:
            result = f"已为整个共享组登记等待 {seconds} 秒；任一聊天收到 @ 可以提前唤醒。"
        return json.dumps({"ok": True, "result": result}, ensure_ascii=False), True

    def control_stop(self) -> tuple[str, bool]:
        """结束本轮共享思考。"""
        self._consecutive_waits = 0
        return json.dumps({"ok": True, "result": "已结束整个共享组的本轮思考。"}, ensure_ascii=False), True

    async def reply(self, member: FocusMember, instruction: str) -> tuple[str, bool]:
        """让目标聊天自己的回复模型生成并发送一条回复。"""
        request = replace(member.request, turn_id=f"focus-{uuid4().hex}")
        is_valid = partial(self.is_member_valid, member)
        runtime = ToolRuntime(
            bot=member.bot,
            event=member.event,
            matcher=FocusMatcher(member.bot, member.target, is_valid=is_valid),
        )
        try:
            text = await chat_service.focus_reply(request, runtime, instruction, is_valid=is_valid)
        except Exception:
            logger.opt(exception=True).warning(f"[ChatFocus] 回复失败: {member.session_id}")
            return _failure("回复生成或发送失败。"), False
        if not text:
            return _failure("回复没有发出。"), False
        self._sent.append(
            FocusEntry([{"role": "assistant", "content": f"[{member.session_id}] {text}"}], {member.session_id})
        )
        return json.dumps({"ok": True, "result": f"已发送到 {member.session_id}：{text}"}, ensure_ascii=False), False

    def _finish_wait(self, reason: str) -> None:
        session_id = self._wait_session_id
        self._wait_until = None
        self._wait_session_id = None
        if session_id in self.members:
            self._sequence += 1
            entry = FocusEntry(
                [
                    {
                        "role": "user",
                        "content": f"[{session_id}]\n[Focus 等待结束] {reason}。请结合所有聊天的新消息继续判断。",
                    }
                ],
                {session_id},
                self._sequence,
            )
            self._pending.setdefault(session_id, []).append(entry)
            self._ready.add(session_id)
        self._event.set()

    def _ensure_worker(self) -> None:
        if self._closed:
            return
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self.run())

    async def run(self) -> None:
        """共享规划循环：空闲等待、整组等待、或处理一批就绪输入；单次失败只退避重试。"""
        try:
            while not self._closed and self.members:
                try:
                    await self._idle_or_turn()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self._failures += 1
                    delay = min(2.0**self._failures, MAX_FAILURE_BACKOFF)
                    logger.opt(exception=True).warning(
                        f"[ChatFocus] 共享规划出错，{delay:.0f}s 后重试: {self.scope_id}"
                    )
                    await asyncio.sleep(delay)
                else:
                    self._failures = 0
        finally:
            await self.checkpoint()

    async def _idle_or_turn(self) -> None:
        """处理一次等待或一轮规划。"""
        if self._wait_until is not None:
            remaining = max(0.0, self._wait_until - time.time())
            self._event.clear()
            try:
                await asyncio.wait_for(self._event.wait(), timeout=remaining)
            except asyncio.TimeoutError:
                self._finish_wait("等待到期")
            return
        if not self._ready:
            self._event.clear()
            await self._event.wait()
            return
        await self._run_turn()

    def _has_unconsidered(self, session_id: str) -> bool:
        threshold = self._considered.get(session_id, 0)
        return any(
            entry.progress.get(session_id, 0) > threshold
            for entry in (*self.entries, *self._pending.get(session_id, []))
        )

    def _take_ready_inputs(self) -> str:
        """取走就绪输入，返回最后就绪的来源会话。"""
        taken: list[FocusEntry] = []
        for session_id in list(self._ready):
            self._ready.discard(session_id)
            if session_id in self.members:
                taken.extend(self._pending.pop(session_id, []))
        taken.sort(key=lambda entry: entry.sequence)
        self.entries.extend(taken)
        return next(iter(taken[-1].session_ids), "") if taken else ""

    async def _run_turn(self) -> None:
        source_session = ""
        for _ in range(MAX_INTERNAL_ROUNDS):
            if self._closed or self._wait_until is not None:
                return
            source_session = self._take_ready_inputs() or source_session
            await self._compact()
            definitions = await self.tools.refresh()
            references = await self._build_references()
            source_session = self._take_ready_inputs() or source_session
            covered = [
                (session_id, entry.sequence)
                for entry in self.entries
                if entry.sequence
                for session_id in entry.session_ids
            ]
            features = {
                "session_count": len({session_id for entry in self.entries for session_id in entry.session_ids}),
                "input_count": len(self.entries),
            }
            tenant = self._routing_session()
            turn_id = f"focus-{uuid4().hex}"
            response, provider, model = await self._chat(self._build_messages(references), definitions)
            if self._closed:
                return
            if self._invalidated:
                # 生成期间有消息被撤回：丢弃基于旧上下文的响应，按新时间线重新规划
                self._invalidated = False
                continue
            for session_id, sequence in covered:
                self._considered[session_id] = max(self._considered.get(session_id, 0), sequence)
            for entry in self.entries:
                entry.prune_inputs(self._considered)
            batch = FocusEntry([response.to_message_dict()], set(self.members))
            self.entries.append(batch)
            try:
                await self.checkpoint()
                paused = await self._execute_tools(batch, response, source_session)
            finally:
                names = [str((call.get("function") or {}).get("name") or "") for call in response.tool_calls]
                try:
                    await chat_repository.save_planner_sample(
                        ChatPlannerSample(
                            tenant=tenant,
                            turn_id=turn_id,
                            action="tool:" + ",".join(names) if names else "no_reply",
                            label=int("reply" in names),
                            features=json.dumps(
                                {**features, "tool_count": len(names), "waiting": self._wait_until is not None}
                            ),
                            provider=provider,
                            model=model,
                        )
                    )
                except Exception:
                    logger.opt(exception=True).debug("[ChatFocus] 规划样本落库失败")
            if self._sent:
                self.entries.extend(self._sent)
                self._sent.clear()
            await self.checkpoint()
            if paused or not response.tool_calls:
                if self._wait_until is None:
                    self._consecutive_waits = 0
                return

    def _routing_session(self) -> str:
        """按接入顺序取第一个有就绪输入的成员，避免依赖集合迭代顺序。"""
        for session_id in self.members:
            if session_id in self._ready:
                return session_id
        return next(iter(self.members), "")

    async def _chat(self, messages: list[dict[str, Any]], definitions: list[dict[str, Any]]) -> tuple[Any, str, str]:
        async with fallback_provider("planner", self._routing_session()) as provider:
            response = await provider.chat(messages=messages, tools=definitions or None)
            return response, provider.provider_id, str(provider.get_model() or "")

    def _build_messages(self, references: list[str]) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = [{"role": "system", "content": self._system_prompt()}]
        for entry in self.entries:
            messages.extend(entry.messages)
        tail = list(references)
        if self._consecutive_waits >= WAIT_REMINDER_AFTER:
            tail.append(_WAIT_REMINDER)
        if tail:
            messages.append({"role": "user", "content": "\n\n".join(tail)})
        return messages

    def _system_prompt(self) -> str:
        lines = [_FOCUS_PROMPT, "", "参与共享的聊天："]
        for session_id, member in self.members.items():
            kind = "群聊" if member.is_group else "私聊"
            name = member.request.user_name or member.request.user_id
            lines.append(f"- {session_id}（{kind}，最近发言者：{name}）")
        return "\n".join(lines)

    async def _build_references(self) -> list[str]:
        """注入每个有新输入聊天的记忆与人格，并标明来源会话。"""
        try:
            chat_cfg = get_config_manager().get_system()["chat"]
        except Exception:
            return []
        behavior = chat_cfg.get("behavior") or {}
        references: list[str] = []
        for session_id, member in list(self.members.items()):
            if not self._has_unconsidered(session_id):
                continue
            request = member.request
            try:
                _, persona_text = build_persona_text(request, chat_cfg, behavior)
                memory = await _memory_block(request, recall_query(request, []))
            except Exception:
                logger.opt(exception=True).debug(f"[ChatFocus] 参考信息构建失败: {session_id}")
                continue
            body = "\n".join(part for part in (persona_text, memory) if part)
            if body:
                references.append(
                    f'<focus_reference session_id="{escape(session_id, quote=True)}">\n{body}\n</focus_reference>'
                )
        return references

    async def _execute_tools(self, batch: FocusEntry, response: Any, source_session: str = "") -> bool:
        paused = False
        for call in response.tool_calls:
            function = call.get("function") or {}
            name = str(function.get("name") or "")
            try:
                arguments = json.loads(function.get("arguments") or "{}")
            except (TypeError, json.JSONDecodeError):
                arguments = {}
            if not isinstance(arguments, dict):
                arguments = {}
            if paused or self._closed:
                content, call_paused = _failure("本批次已暂停，此工具未执行。"), False
            else:
                content, call_paused = await self.tools.invoke(name, arguments, source_session)
                paused = paused or call_paused
                if name != WAIT_TOOL:
                    self._consecutive_waits = 0
            batch.messages.append({"role": "tool", "tool_call_id": call.get("id"), "name": name, "content": content})
            await self.checkpoint()
        return paused

    async def _compact(self) -> None:
        """超过窗口时把最旧的完整片段压成状态摘要。"""
        if len(self.entries) <= 1:
            return
        limit = self._max_context()
        size = sum(len(entry.messages) for entry in self.entries)
        split = 0
        while split < len(self.entries) - 1 and size > limit:
            size -= len(self.entries[split].messages)
            split += 1
        if split <= 0:
            return
        old = self.entries[:split]
        try:
            summary = await compact_messages(self.scope_id, [message for entry in old for message in entry.messages])
        except Exception:
            summary = ""
        related = set().union(*(entry.session_ids for entry in old))
        messages = [{"role": "system", "content": f"{SUMMARY_HEADER}\n{summary}"}] if summary else []
        self.entries[:split] = [FocusEntry(messages, related)]

    def _max_context(self) -> int:
        try:
            value = get_config_manager().get_system()["chat"].get("max_context_size", 30)
        except Exception:
            return 30
        return max(2, int(value)) if isinstance(value, int) and not isinstance(value, bool) else 30

    async def stop(self) -> None:
        """停止共享组并保存最后状态。"""
        self._closed = True
        worker = self._worker
        if worker is not None and worker is not asyncio.current_task():
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        self._worker = None
        for task in self._debounce_tasks.values():
            task.cancel()
        await asyncio.gather(*self._debounce_tasks.values(), return_exceptions=True)
        self._debounce_tasks.clear()
        await self.checkpoint()
