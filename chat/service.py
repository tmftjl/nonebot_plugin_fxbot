"""ChatService 编排：把一轮消息交给提示组装、规划、生成、发送与持久化。

目录结构参照 MaiBot（commit 062d45c，`src/chat/`）：消息接收与发送在 `message_receive/`，
提示与生成在 `replyer/`，规划分派在 `heart_flow/`。
"""

from __future__ import annotations

import time
import asyncio
from typing import Any
from datetime import datetime
from contextlib import nullcontext
from collections.abc import Callable

from nonebot import logger

from .tools import ToolRuntime, subagent as subagent_module, tool_result_store
from .trace import TurnTrace, turn_trace
from .types import MentionRef, ChatRequest, ChatResponse, is_proactive
from .memory import state as memory_state
from .vision import describe_images, vision_configured
from ..config import get_manager as get_config_manager
from .replyer import prompt, generator
from .session import ChatSessionStore, history_limit, default_session_store
from ..adapter import get_bot_group_role
from ..utils.tz import now
from .providers import LLMResponse
from .heart_flow import plan_flow, conversation
from .memory.inject import learning_context
from .work_schedule import can_reply
from .behavior.quote import forget_counter
from .amemorix.inject import memory_block
from .context_summary import clear_summary, current_summary, compact_messages
from .message_context import message_metadata
from .message_receive import sender as sender_module, history as history_module
from .memory.filtering import sanitize_line
from .behavior.debounce import default_debounce_registry
from .providers.routing import TaskProvider, routed_task, fallback_provider, candidate_providers
from .behavior.reply_gate import default_reply_gate_registry


def _cached_image_descriptions(request: ChatRequest, history: list[dict[str, Any]]) -> list[str] | None:
    """复用当前消息已经完成的识图结果，避免 Planner 和回复器重复调用视觉模型。"""
    if not request.images:
        return None
    cached = request.metadata.get("recognized_images")
    if isinstance(cached, dict) and cached.get("sources") == request.images:
        descriptions = cached.get("descriptions")
        if isinstance(descriptions, list) and len(descriptions) == len(request.images) and any(descriptions):
            return list(descriptions)
    if request.batched or len(request.source_message_ids) > 1:
        return None
    identity = request.message_id or request.dedupe_key
    for message in reversed(history):
        if message.get("role") != "user" or identity not in message.get("_source_ids", []):
            continue
        descriptions = message.get("_image_descriptions")
        if isinstance(descriptions, list) and len(descriptions) == len(request.images) and any(descriptions):
            return list(descriptions)
    return None


async def _describe_images(request: ChatRequest) -> list[str]:
    """图片描述按 vision 路由单独取 Provider；超过单条识图上限时只留占位。"""
    settings = get_config_manager().get_system()["chat"].get("message_receive") or {}
    if len(request.images) > int(settings.get("image_parse_threshold", 5)):
        return []
    async with fallback_provider("vision", request.session_id) as provider:
        return await describe_images(provider, request.images)


def _image_wait_seconds() -> float:
    """生成前等待图片描述的最长秒数；0 表示不等待。"""
    try:
        value = get_config_manager().get_system()["chat"]["visual"]["wait_image_recognize_max_time"]
        return max(0.0, float(value))
    except (KeyError, TypeError, ValueError):
        return 32.0


async def _wait_image_descriptions(task: asyncio.Task | None) -> list[str]:
    """生成前取识图结果；拿到描述后本轮直接用文字代替图片。"""
    seconds = _image_wait_seconds()
    if task is None or seconds <= 0:
        return []
    try:
        return await asyncio.wait_for(asyncio.shield(task), timeout=seconds)
    except asyncio.TimeoutError:
        logger.debug("[Chat] 图片识别超时，本轮先保留原图")
        return []
    except Exception:
        logger.opt(exception=True).debug("[Chat] 图片识别失败，本轮先保留原图")
        return []


async def _memory_block(request: ChatRequest, query: str = "", trace: dict[str, Any] | None = None) -> str:
    """读取 A_Memorix 长期记忆，学习参考按 Planner 和 Replyer 分别组装。"""
    try:
        block = await memory_block(request)
    except Exception:
        logger.opt(exception=True).debug("[Chat] 长期记忆读取失败，本轮不注入")
        block = ""
    if trace is not None:
        trace["memory_chars"] = len(block)
    return block


async def _learning_reference(
    request: ChatRequest,
    history: list[dict[str, Any]],
    *,
    reply_reference: str | None = None,
    trace: dict[str, Any] | None = None,
) -> str:
    """黑话只供 Planner 理解，表达只供 Replyer 使用，重建上下文沿用本轮已注入词条。"""
    intent = request.metadata.get("expression_intent")
    intent_text = "\n".join(f"{key}：{value}" for key, value in intent.items()) if isinstance(intent, dict) else ""
    query = "\n\n".join(part for part in (intent_text, reply_reference or "") if part)
    try:
        return await learning_context(
            request.session_id,
            query,
            phase="planner" if reply_reference is None else "reply",
            history=history,
            references=request.metadata.setdefault("planner_jargon_references", {}),
            trace=trace,
        )
    except Exception:
        logger.opt(exception=True).debug("[Chat] 学习参考读取失败，本轮不注入")
        return ""


class ChatService:
    """AI 对话编排服务。"""

    def __init__(self, session_store: ChatSessionStore = default_session_store) -> None:
        self.session_store = session_store
        self._active_requests: dict[str, ChatRequest] = {}

    async def queue_message(self, request: ChatRequest, runtime: ToolRuntime | None = None) -> bool:
        """运行中的会话接收后续消息，由同一个 Planner 消费。"""
        active = self._active_requests.get(request.session_id)
        if active is None:
            return False
        active.metadata["planner_latest_request"] = request
        if runtime is not None:
            active.metadata["planner_latest_runtime"] = runtime
            active.metadata["planner_runtime"] = runtime
        if request.metadata.get("history_recorded"):
            active.metadata["planner_pending"] = True
            return True
        await self.record_group_message(
            request.session_id,
            request.user_id,
            request.user_name,
            request.text,
            is_group=request.is_group,
            images=request.images,
            mentions=request.mentions,
            dedupe_key=request.dedupe_key,
            platform=request.platform,
            event_id=request.event_id,
            message_id=request.message_id,
            reply_to_message_id=request.reply_to_message_id,
            occurred_at=request.occurred_at,
            has_at_bot=bool(request.metadata.get("has_at_bot")),
            sender_role=request.metadata.get("sender_role"),
            quote_snapshot=request.metadata.get("quote_snapshot"),
        )
        request.metadata["history_recorded"] = True
        request.metadata["history_appended"] = True
        if self._active_requests.get(request.session_id) is not active:
            return False
        active.metadata["planner_pending"] = True
        return True

    async def process(self, request: ChatRequest, runtime: ToolRuntime | None = None) -> ChatResponse:
        """处理一次对话请求；规划等待时先让出轮次锁，醒后重新规划。"""
        while True:
            async with self.session_store.turn_lock(request.session_id):
                response = await self._process_locked(request, runtime)
                pause = float(response.metadata.get("wait_seconds") or 0)
                if not pause:
                    return response
            if not await self._pause_turn(request, pause):
                return ChatResponse(text="", epoch=request.accepted_epoch)

    async def process_and_send(self, request: ChatRequest, runtime: ToolRuntime | None = None) -> bool:
        """生成并逐段发送；生成到发送结束之间持有轮次锁，规划等待期间让出。"""
        if await self.queue_message(request, runtime):
            return False
        self._active_requests[request.session_id] = request
        try:
            while True:
                async with self.session_store.turn_lock(request.session_id):
                    current_runtime = request.metadata.get("planner_runtime", runtime)
                    response = await self._process_locked(request, current_runtime)
                    pause = float(response.metadata.get("wait_seconds") or 0)
                    if not pause:
                        sent = await self._send_generated(request, response, current_runtime)
                        if request.metadata.pop("planner_pending", False):
                            continue
                        return sent
                if not await self._pause_turn(request, pause):
                    return False
        finally:
            if self._active_requests.get(request.session_id) is request:
                self._active_requests.pop(request.session_id, None)

    async def _send_generated(self, request: ChatRequest, response: ChatResponse, runtime: ToolRuntime | None) -> bool:
        """发送已生成的回复；空正文、失效轮次或缺少发送目标时结束本轮。"""
        if response.metadata.get("planner_sent"):
            return True
        if not response.text or not self.is_current(request.session_id, response.epoch):
            if response.text:
                await sender_module.record_send_trace(request, response, cancelled=True)
            if not response.text and is_proactive(request):
                default_reply_gate_registry.get(request.session_id).record_idle()
            return False
        if not self.is_latest(request):
            logger.debug(f"[Chat] 回复已被新触发取代，取消发送: session={request.session_id}")
            await sender_module.record_send_trace(request, response, cancelled=True)
            return False
        matcher = getattr(runtime, "matcher", None)
        if matcher is None:
            logger.warning("[Chat] 缺少发送目标，已跳过发送")
            await sender_module.record_send_trace(request, response, error="缺少发送目标", failed_index=0)
            return False
        return bool(await self._send_parts(request, response, runtime, matcher))

    async def _pause_turn(self, request: ChatRequest, seconds: float) -> bool:
        """锁外等待；被同一用户的新触发取代时作废本轮，新消息可提前唤醒。"""
        epoch = request.accepted_epoch or 0
        deadline = time.monotonic() + seconds
        while True:
            if not can_reply():
                return False
            if not self._alive(request, epoch):
                await self._mark_turn_aborted(request, "等待期间被新触发取代")
                return False
            if (not request.is_group or plan_flow.steering_enabled()) and self.session_store.activity(
                request.session_id
            ) != request.activity_seq:
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return True
            await asyncio.sleep(min(1.0, remaining))

    async def focus_reply(
        self, request: ChatRequest, runtime: ToolRuntime, instruction: str, *, is_valid: Callable[[], bool]
    ) -> str:
        """Focus 规划器指定方向后，由该聊天自己的回复模型生成并发送；返回已发出的正文。"""
        request.metadata["planner_reply"] = True
        async with self.session_store.turn_lock(request.session_id):
            if not is_valid():
                return ""
            response = await self._process_locked(request, runtime, focus_instruction=instruction)
            if pictures := request.metadata.get("reply_pictures"):
                response.metadata["attach_pictures"] = pictures
            if digest := request.metadata.get("reply_emoji"):
                response.metadata["attach_emoji"] = digest
            if not is_valid():
                if response.text:
                    await sender_module.record_send_trace(request, response, cancelled=True)
                return ""
            if not response.text or not self.is_current(request.session_id, response.epoch):
                if response.text:
                    await sender_module.record_send_trace(request, response, cancelled=True)
                return ""
            if not self.is_latest(request):
                logger.debug(f"[Chat] Focus 回复已被新触发取代，取消发送: session={request.session_id}")
                await sender_module.record_send_trace(request, response, cancelled=True)
                return ""
            matcher = getattr(runtime, "matcher", None)
            if matcher is None:
                await sender_module.record_send_trace(request, response, error="缺少发送目标", failed_index=0)
                return ""
            sent_text = await self._send_parts(request, response, runtime, matcher)
            if not is_valid():
                return ""
            return sent_text

    async def _process_locked(
        self, request: ChatRequest, runtime: ToolRuntime | None, *, focus_instruction: str | None = None
    ) -> ChatResponse:
        """调用方已持有轮次锁。"""
        chat_cfg = get_config_manager().get_system()["chat"]
        if not can_reply(chat_cfg):
            return ChatResponse(text="", epoch=self.session_store.epoch(request.session_id))
        await self._hydrate(request.session_id, history_limit(chat_cfg, request.is_group))
        if request.accepted_epoch is None:
            request.accepted_epoch = self.session_store.epoch(request.session_id)
        if not self.is_current(request.session_id, request.accepted_epoch):
            logger.debug(f"[Chat] 清空后跳过排队轮次: session={request.session_id}")
            return ChatResponse(text="", epoch=request.accepted_epoch)
        try:
            response = await self._process(request, runtime, focus_instruction=focus_instruction)
        except Exception as exc:
            await self._mark_turn_failed(request, exc)
            raise
        return response

    async def _send_emoji(
        self, request: ChatRequest, response: ChatResponse, runtime: ToolRuntime | None, matcher: Any, sent: int
    ) -> None:
        """正文发送后补一条表情；失败只降级，不影响正文。"""
        await sender_module.send_emoji(self, request, response, runtime, matcher, sent)

    async def _send_parts(
        self, request: ChatRequest, response: ChatResponse, runtime: ToolRuntime | None, matcher: Any
    ) -> str:
        """逐段发送；同租户发送期间不交错，任何一段失效后不再继续。"""
        return await sender_module.send_parts(self, request, response, runtime, matcher)

    async def ensure_hydrated(self, session_id: str, *, is_group: bool = True) -> None:
        """接入触发前恢复历史与持久化代次，供路由捕获准确的 generation。"""
        try:
            chat_cfg = get_config_manager().get_system()["chat"]
        except Exception:
            return
        await self._hydrate(session_id, history_limit(chat_cfg, is_group))

    async def clear_session(self, session_id: str) -> None:
        """清空会话历史；内存、数据库、待合并批次与在途轮次一起失效。"""
        from .memory.trimmed import trimmed_learning
        from .behavior.turn_scheduler import normal_scheduler

        normal_scheduler.clear(session_id)
        trimmed_learning.clear(session_id)
        self._active_requests.pop(session_id, None)
        default_debounce_registry.clear(session_id)
        default_reply_gate_registry.clear(session_id)
        forget_counter(session_id)
        try:
            from .focus import focus_mode_manager

            await focus_mode_manager.clear_context(session_id)
        except Exception:
            logger.opt(exception=True).warning("[Chat] 清理 Focus 上下文失败")
        async with self.session_store.lock(session_id):
            subagent_module.subagent_tasks.cancel_session(session_id)
            self.session_store.clear(session_id)
            tool_result_store.clear(session_id)
            clear_summary(session_id)
            if not memory_state.is_ready():
                return
            try:
                generation = await history_module.clear_tenant(session_id)
            except Exception:
                logger.opt(exception=True).warning("[Chat] 清空数据库历史失败，重启后可能恢复")
                return
            self.session_store.set_epoch(session_id, generation)

    async def _hydrate(self, session_id: str, limit: int) -> None:
        """首次访问会话时从数据库恢复最近历史，不覆盖已到的新消息。"""
        await history_module.hydrate(self, session_id, limit)

    async def persist_batch(self, requests: list[ChatRequest]) -> None:
        """缓冲里的原始消息逐条落库；合并内容只作为本轮 user 条目。"""
        if not requests or not memory_state.is_ready():
            return
        # 先恢复旧历史，避免刚写入的本轮消息被 process 的 _hydrate 当作背景重复注入
        chat_cfg = get_config_manager().get_system()["chat"]
        await self._hydrate(requests[0].session_id, history_limit(chat_cfg, requests[0].is_group))
        await history_module.persist_batch(requests)

    async def _persist_turn_start(
        self, request: ChatRequest, content: str, epoch: int
    ) -> tuple[int, int | None] | None:
        """写入用户消息、轮次和用户条目；失败只降级本轮持久化。"""
        return await history_module.persist_turn_start(request, content, epoch)

    async def _persist_turn_end(
        self,
        request: ChatRequest,
        persisted: tuple[int, int | None] | None,
        response: LLMResponse,
        provider_name: str,
        content: str,
        descriptions: list[str],
        parts: list[str],
        generated: str | None = None,
    ) -> list[int] | None:
        """生成完成后写 generated_text 与逐段待发送的助手条目。"""
        if request.accepted_epoch is not None and not self.is_current(request.session_id, request.accepted_epoch):
            logger.debug("[Chat] 跳过过期生成结果: tenant={} generation={}", request.session_id, request.accepted_epoch)
            return None
        return await history_module.persist_turn_end(
            request,
            persisted,
            response,
            provider_name,
            content,
            descriptions,
            parts,
            generated,
        )

    async def mark_sending(self, request: ChatRequest, response: ChatResponse, index: int = 0) -> None:
        """发送前把轮次与第 index 段标为发送中。"""
        if not self.is_current(request.session_id, response.epoch):
            logger.debug("[Chat] 跳过过期发送中状态: tenant={} generation={}", request.session_id, response.epoch)
            return
        await history_module.mark_sending(request, response, index)

    async def mark_sent(
        self,
        request: ChatRequest,
        response: ChatResponse,
        index: int = 0,
        platform_message_id: str | None = None,
        content: str | None = None,
    ) -> None:
        """发送成功后把该段写入可见历史；重复调用不重复追加。"""
        chat_cfg = get_config_manager().get_system()["chat"]
        await history_module.mark_sent(
            self,
            request,
            response,
            index,
            platform_message_id,
            content=content,
            limit=history_limit(chat_cfg, request.is_group),
        )

    async def mark_send_aborted(
        self, request: ChatRequest, response: ChatResponse, index: int = 0, error: str = "发送前已被新触发取代"
    ) -> None:
        """发送前复核失败：从第 index 段起作废，至少有一段成功时轮次标为部分发送。"""
        if not self.is_current(request.session_id, response.epoch):
            logger.debug("[Chat] 跳过过期发送作废状态: tenant={} generation={}", request.session_id, response.epoch)
            return
        await history_module.mark_send_aborted(request, response, index, error)

    async def mark_send_failed(
        self, request: ChatRequest, response: ChatResponse, exc: Exception, index: int = 0
    ) -> None:
        """发送失败时保留生成文本；已成功的前几段不回滚，轮次按是否发过区分。"""
        if not self.is_current(request.session_id, response.epoch):
            logger.debug("[Chat] 跳过过期发送失败状态: tenant={} generation={}", request.session_id, response.epoch)
            return
        await history_module.mark_send_failed(request, response, exc, index)

    async def _mark_turn_aborted(self, request: ChatRequest, error: str = "") -> None:
        """清空或打断后作废在途轮次。"""
        await history_module.mark_turn_aborted(request, error)

    async def _mark_turn_failed(self, request: ChatRequest, exc: Exception) -> None:
        """记录本轮失败；轮次行不存在时什么也不做。"""
        await history_module.mark_turn_failed(request, exc)

    def is_current(self, session_id: str, epoch: int) -> bool:
        """判断轮次是否仍属于当前会话，清空后的旧轮次不可继续生效。"""
        return self.session_store.epoch(session_id) == epoch

    def is_latest(self, request: ChatRequest) -> bool:
        """该轮次之后没有同一触发者的新触发。"""
        if request.metadata.get("planner_loop"):
            return True
        if not request.trigger_seq:
            return True
        owner = request.trigger_owner or request.user_id
        return self.session_store.is_latest_seq(request.session_id, owner, request.trigger_seq)

    def _alive(self, request: ChatRequest, epoch: int) -> bool:
        """轮次仍有效：未被清空，也没有被同一用户的新触发取代。"""
        return self.is_current(request.session_id, epoch) and self.is_latest(request)

    async def record_group_message(
        self,
        session_id: str,
        user_id: str,
        user_name: str,
        text: str,
        *,
        is_group: bool,
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
        images = list(images or [])
        if not text and not images and not has_image:
            return
        chat_cfg = get_config_manager().get_system()["chat"]
        limit = history_limit(chat_cfg, is_group)
        if limit <= 0:
            return
        await self._hydrate(session_id, limit)
        await history_module.record_group_message(
            self,
            session_id,
            user_id,
            user_name,
            text,
            chat_cfg=chat_cfg,
            limit=limit,
            images=images,
            has_image=has_image,
            mentions=mentions,
            dedupe_key=dedupe_key,
            platform=platform,
            event_id=event_id,
            message_id=message_id,
            reply_to_message_id=reply_to_message_id,
            occurred_at=occurred_at,
            memory_images=memory_images,
            has_at_bot=has_at_bot,
            sender_role=sender_role if is_group else None,
            quote_snapshot=quote_snapshot,
        )
        if active := self._active_requests.get(session_id):
            active.metadata["planner_pending"] = True

    async def _process(
        self, request: ChatRequest, runtime: ToolRuntime | None, *, focus_instruction: str | None = None
    ) -> ChatResponse:
        """完成一轮对话；全程持有轮次锁，仅在读写历史时持有状态锁。"""
        candidates = candidate_providers("reply", request.session_id)
        trace: dict[str, Any] = {
            "request_preview": sanitize_line(request.text, 200),
            "trigger_kind": request.trigger_kind,
        }
        state = turn_trace(request)
        if state is None:
            state = TurnTrace(request.turn_id)
            request.metadata["_turn_trace"] = state
        owns_trace = state.turn_id == request.turn_id
        started = time.monotonic()
        error = ""
        providers: TaskProvider | None = None
        try:
            async with TaskProvider(candidates, task="reply") as providers:
                return await self._process_with_provider(
                    request, runtime, providers, providers.name, trace, focus_instruction=focus_instruction
                )
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            provider_name = providers.name if providers is not None else candidates[0]
            if focus_instruction is not None:
                state.add_stage(
                    "replyer",
                    "回复生成",
                    int((time.monotonic() - started) * 1000),
                    provider=provider_name,
                    error=error,
                    target_message_id=request.message_id or "",
                    budget=dict(trace.get("budget") or {}),
                    injected=dict(trace.get("injected") or {}),
                )
            if owns_trace:
                snapshot = state.snapshot()
                trace.setdefault("budget", {}).update(snapshot)
                failures = "\n".join(dict.fromkeys(stage["error"] for stage in state.stages if stage.get("error")))
                await history_module.record_trace(
                    request, provider_name, snapshot["timings"]["total_ms"], error or failures, trace
                )
                state.saved = True

    async def _process_with_provider(
        self,
        request: ChatRequest,
        runtime: ToolRuntime | None,
        provider: Any,
        provider_name: str = "",
        trace: dict[str, Any] | None = None,
        *,
        focus_instruction: str | None = None,
    ) -> ChatResponse:
        chat_cfg = get_config_manager().get_system()["chat"]
        if focus_instruction is None:
            request.metadata["planner_loop"] = True
        behavior = chat_cfg.get("behavior") or {}
        limit = history_limit(chat_cfg, request.is_group)
        if trace is not None:
            trace["budget"] = {"max_context_size": limit}
        runtime = runtime or ToolRuntime()
        proactive = is_proactive(request) and focus_instruction is None
        user_content = prompt.build_user_content(request)
        current_content = prompt.truncate_content(user_content)
        request.metadata["bot_group_role"] = await get_bot_group_role(getattr(runtime, "bot", None), request.group_id)
        effective_persona, persona_text = prompt.build_persona_text(request, chat_cfg, behavior)
        request.metadata["bot_name"] = effective_persona
        request.metadata["bot_id"] = str(getattr(getattr(runtime, "bot", None), "self_id", "") or "")

        description_task = None
        try:
            async with self.session_store.lock(request.session_id):
                self.session_store.trim(request.session_id, limit)
                history = [m for m in self.session_store.get(request.session_id) if m.get("role") != "system"]
                if focus_instruction is not None:
                    history = [
                        message
                        for message in history
                        if not message.get("_planner_internal")
                        and not message.get("_reference")
                        and (message.get("_sender_id") or message.get("_self_message"))
                        and message.get("role") in {"user", "assistant"}
                        and not message.get("tool_calls")
                    ]
                while history and history[0].get("role") != "user" and not history[0].get("_self_message"):
                    history.pop(0)
                epoch = (
                    request.accepted_epoch
                    if request.accepted_epoch is not None
                    else self.session_store.epoch(request.session_id)
                )
                entry = prompt.strip_images([{"role": "user", "content": user_content}])[0]
                entry["_sender_id"] = request.user_id
                entry.update(
                    message_metadata(
                        timestamp=request.occurred_at,
                        sender_id=request.user_id,
                        sender_name=request.user_name,
                        quote_id=request.reply_to_message_id or "",
                        at_bot=bool(request.metadata.get("has_at_bot")),
                        sender_role=request.metadata.get("sender_role") if request.is_group else None,
                        quote_snapshot=request.metadata.get("quote_snapshot"),
                    )
                )
                source_ids = request.source_message_ids or ([request.message_id] if request.message_id else [])
                if source_ids:
                    # 撤回通知按平台消息 ID 找到并丢弃这一条
                    entry["_source_ids"] = [str(value) for value in source_ids]
                recorded = request.metadata.get("history_recorded") or request.metadata.get("history_appended")
                if not recorded:
                    # Focus 上下文消息已由 record_group_message 写入，等待后重新规划也不重复写内存历史
                    self.session_store.append(request.session_id, entry, limit=limit)
                    request.metadata["history_appended"] = True
                dropped = self.session_store.take_dropped(request.session_id)
            descriptions = _cached_image_descriptions(request, history)
            if descriptions is None and vision_configured(chat_cfg) and request.images:
                description_task = asyncio.create_task(_describe_images(request))
            persisted = await self._persist_turn_start(request, str(entry["content"]), epoch)
            if not self._alive(request, epoch):
                await self._mark_turn_aborted(request, "生成前已被新触发取代")
                return ChatResponse(text="", epoch=epoch)

            summary = ""
            if limit > 0:
                if dropped:
                    summary = await compact_messages(request.session_id, dropped)
                else:
                    summary = current_summary(request.session_id)
            if not self._alive(request, epoch):
                await self._mark_turn_aborted(request, "生成前已被新触发取代")
                return ChatResponse(text="", epoch=epoch)
            memory_block = await _memory_block(request, prompt.recall_query(request, history), trace)
            if descriptions is None:
                descriptions = await _wait_image_descriptions(description_task)
            if descriptions:
                request.metadata["recognized_images"] = {
                    "sources": list(request.images),
                    "descriptions": list(descriptions),
                }
                # 回复模型按纯文本处理：有描述就把本轮图片换成文字，历史与本轮保持同一种写法
                entry["content"] = prompt.strip_images([entry], descriptions)[0]["content"]
                current_content = prompt.truncate_content(entry["content"])
            messages = prompt.assemble_messages(
                request,
                history,
                persona_text,
                memory_block,
                current_content,
                context_summary=summary,
            )
            learning_history = list(history)
            current_ids = set(entry.get("_source_ids", []))
            if not current_ids or not any(current_ids.intersection(item.get("_source_ids", [])) for item in history):
                learning_history.append(entry)
            reference = await _learning_reference(
                request, learning_history, reply_reference=focus_instruction, trace=trace
            )
            if reference:
                messages.append({"role": "user", "content": reference})
            if focus_instruction:
                messages.append({"role": "user", "content": focus_instruction.strip()})
                is_self = bool(request.metadata.get("bot_id")) and request.user_id == request.metadata["bot_id"]
                target_id = request.message_id or request.dedupe_key
                target_note = (
                    f"你想要补充说明你自己（{effective_persona}） 发送的 msg_id为 {target_id} 的消息，"
                    "你可以在这条目标消息的基础上补充发言，不要把你自己的发言当成别人的发言。"
                    if is_self
                    else f"你想要回复的消息是 {request.user_name or request.user_id} 发送的 msg_id为 "
                    f"{target_id} 的消息，你这次要回复的就是这条目标消息，不要把其他历史消息当成当前回复对象。"
                )
                quote_note = ""
                if quotes := request.metadata.get("target_quote_ids"):
                    quote_note = f"- quote={','.join(quotes)}\n"
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"当前时间：{now():%Y-%m-%d %H:%M:%S}\n\n"
                            f"{target_note}\n{quote_note}"
                            f"- 发言内容：{' '.join(request.text.split())[:300] or '[无可见文本内容]'}\n\n"
                            "请自然地回复。不要输出多余说明、括号、@ 或额外标记，只输出实际要发言的内容。"
                        ),
                    }
                )
                if style := prompt.requested_reply_style(str(request.metadata.get("requested_reply_style") or "")):
                    messages.append({"role": "user", "content": style})
            if trace is not None:
                trace["budget"]["memory_chars"] = len(memory_block)
                trace["budget"]["summary_chars"] = len(summary)

            allowed_names, tools = await generator.allowed_tools(
                runtime, proactive=proactive, session_id=request.session_id
            )
            if trace is not None:
                trace["budget"]["tools"] = len(allowed_names)
            context = plan_flow.PlanContext(
                request=request,
                messages=messages,
                entry=entry,
                epoch=epoch,
                runtime=runtime,
                allowed_names=allowed_names,
                persona_text=persona_text,
                memory_block=memory_block,
                current_content=current_content,
                limit=limit,
                provider_name=provider_name,
            )
            if focus_instruction is None:
                return await conversation.run(self, context)
            messages = context.messages

            if style_note := prompt.temporary_style_note(behavior):
                prompt.append_note(messages, style_note)
            response = await generator.generate(
                self, request, runtime, provider, messages, tools, allowed_names, epoch, limit
            )
            post_cfg = chat_cfg.get("response_post_process") or {}
            splitter_cfg = chat_cfg.get("response_splitter")
            # 上游快速任务未分配时使用 utils，规则断句不额外租用模型。
            splitter_scope = (
                fallback_provider(routed_task("fast_model", "utils"), request.session_id)
                if isinstance(splitter_cfg, dict)
                and splitter_cfg.get("mode") == "llm"
                and splitter_cfg.get("enable", True)
                else nullcontext(provider)
            )
            async with splitter_scope as splitter_provider:
                response, segments, generated_text = await generator.shape_reply(
                    response,
                    provider=splitter_provider,
                    persona=effective_persona,
                    post_enabled=bool(post_cfg.get("enable_response_post_process", True)),
                    splitter=splitter_cfg if isinstance(splitter_cfg, dict) else None,
                )
            if not self._alive(request, epoch):
                reason = "会话已清空" if not self.is_current(request.session_id, epoch) else "已被新触发取代"
                await self._mark_turn_aborted(request, reason)
                return ChatResponse(text=response.content, raw=response, epoch=epoch)
            parts = [segment.text for segment in segments]
            provider_name = str(getattr(provider, "provider_id", "") or provider_name)
            item_ids = await self._persist_turn_end(
                request,
                persisted,
                response,
                provider_name,
                str(entry["content"]),
                descriptions,
                parts,
                generated_text,
            )
            result = ChatResponse(text=response.content, raw=response, epoch=epoch)
            if parts:
                result.metadata["reply_parts"] = parts
            if item_ids is not None:
                result.metadata["turn_item_ids"] = item_ids
            if persisted is not None:
                result.metadata["turn_db_id"] = persisted[0]
            return result
        finally:
            if description_task is not None:
                if not description_task.done():
                    description_task.cancel()
                await asyncio.gather(description_task, return_exceptions=True)


chat_service = ChatService()
