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
from .types import MentionRef, ChatRequest, ChatResponse, is_proactive
from .memory import state as memory_state
from .vision import describe_images, vision_configured
from ..config import get_manager as get_config_manager
from .planner import Plan
from .replyer import prompt, generator
from .session import ChatSessionStore, history_limit, default_session_store
from .providers import LLMResponse
from .heart_flow import plan_flow
from .memory.inject import learning_context
from .behavior.quote import forget_counter
from .amemorix.inject import memory_block
from .context_summary import clear_summary, current_summary, compact_messages
from .message_receive import sender as sender_module, history as history_module
from .memory.filtering import sanitize_line
from .behavior.debounce import default_debounce_registry
from .providers.routing import FallbackProvider, routed_task, fallback_provider, candidate_providers
from .behavior.reply_gate import default_reply_gate_registry


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
    """读取 A_Memorix 长期记忆与群学习参考；失败只降级注入，不影响本轮对话。"""
    try:
        block = await memory_block(request)
    except Exception:
        logger.opt(exception=True).debug("[Chat] 长期记忆读取失败，本轮不注入")
        block = ""
    try:
        learning = await learning_context(request.session_id, query, trace=trace)
    except Exception:
        logger.opt(exception=True).debug("[Chat] 群学习参考读取失败，本轮不注入")
        learning = ""
    if trace is not None:
        trace["memory_chars"] = len(block)
        trace["learning_chars"] = len(learning)
    return "\n".join(part for part in (block, learning) if part)


class ChatService:
    """AI 对话编排服务。"""

    def __init__(self, session_store: ChatSessionStore = default_session_store) -> None:
        self.session_store = session_store

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
        while True:
            async with self.session_store.turn_lock(request.session_id):
                response = await self._process_locked(request, runtime)
                pause = float(response.metadata.get("wait_seconds") or 0)
                if not pause:
                    return await self._send_generated(request, response, runtime)
            if not await self._pause_turn(request, pause):
                return False

    async def _send_generated(self, request: ChatRequest, response: ChatResponse, runtime: ToolRuntime | None) -> bool:
        """发送已生成的回复；空正文、失效轮次或缺少发送目标时结束本轮。"""
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
            if not self._alive(request, epoch):
                await self._mark_turn_aborted(request, "等待期间被新触发取代")
                return False
            if plan_flow.steering_enabled() and self.session_store.activity(request.session_id) != request.activity_seq:
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return True
            await asyncio.sleep(min(1.0, remaining))

    async def focus_reply(
        self, request: ChatRequest, runtime: ToolRuntime, instruction: str, *, is_valid: Callable[[], bool]
    ) -> str:
        """Focus 规划器指定方向后，由该聊天自己的回复模型生成并发送；返回已发出的正文。"""
        async with self.session_store.turn_lock(request.session_id):
            if not is_valid():
                return ""
            response = await self._process_locked(request, runtime, focus_instruction=instruction)
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
        )

    async def _maybe_plan(
        self,
        request: ChatRequest,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        history_len: int = 0,
        memory_chars: int = 0,
        alive: Any = None,
    ) -> Plan | None:
        """复杂模式下调用一次规划器；关闭或失败时返回 None，回退普通回复。"""
        return await plan_flow.maybe_plan(
            request,
            messages,
            tools,
            history_len=history_len,
            memory_chars=memory_chars,
            alive=alive,
        )

    async def _plan_with_steering(
        self,
        context: plan_flow.PlanContext,
        tools: list[dict[str, Any]],
        *,
        history_len: int = 0,
        memory_chars: int = 0,
    ) -> Plan | None:
        """规划期间收到新消息时，用最新历史重新规划一次。"""
        request = context.request

        def alive() -> bool:
            return self._alive(request, context.epoch)

        steering = plan_flow.steering_enabled()
        plan: Plan | None = None
        for _ in range(2):
            version = self.session_store.activity(request.session_id) if steering else 0
            plan = await self._maybe_plan(
                request,
                context.messages,
                tools,
                history_len=history_len,
                memory_chars=memory_chars,
                alive=lambda: alive() and (not version or self.session_store.activity(request.session_id) == version),
            )
            if plan is not None or not version or not alive():
                return plan
            if self.session_store.activity(request.session_id) == version:
                return plan
            context.messages = await plan_flow.rebuild_messages(self, context)
        return plan

    async def _process(
        self, request: ChatRequest, runtime: ToolRuntime | None, *, focus_instruction: str | None = None
    ) -> ChatResponse:
        """完成一轮对话；全程持有轮次锁，仅在读写历史时持有状态锁。"""
        candidates = candidate_providers("reply", request.session_id)
        trace: dict[str, Any] = {
            "request_preview": sanitize_line(request.text, 200),
            "trigger_kind": request.trigger_kind,
        }
        started = time.monotonic()
        error = ""
        providers: FallbackProvider | None = None
        try:
            async with FallbackProvider(candidates, task="reply") as providers:
                return await self._process_with_provider(
                    request, runtime, providers, providers.name, trace, focus_instruction=focus_instruction
                )
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            await history_module.record_trace(
                request,
                providers.name if providers is not None else candidates[0],
                int((time.monotonic() - started) * 1000),
                error,
                trace,
            )

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
        behavior = chat_cfg.get("behavior") or {}
        limit = history_limit(chat_cfg, request.is_group)
        if trace is not None:
            trace["budget"] = {"max_context_size": limit}
        runtime = runtime or ToolRuntime()
        proactive = is_proactive(request) and focus_instruction is None
        user_content = prompt.build_user_content(request)
        current_content = prompt.truncate_content(user_content)
        effective_persona, persona_text = prompt.build_persona_text(request, chat_cfg, behavior)

        # 描述与主对话并发执行，避免图片消息多等一次模型调用
        description_task = (
            asyncio.create_task(_describe_images(request)) if vision_configured(chat_cfg) and request.images else None
        )
        try:
            async with self.session_store.lock(request.session_id):
                self.session_store.trim(request.session_id, limit)
                history = [m for m in self.session_store.get(request.session_id) if m.get("role") != "system"]
                while history and history[0].get("role") != "user":
                    history.pop(0)
                epoch = (
                    request.accepted_epoch
                    if request.accepted_epoch is not None
                    else self.session_store.epoch(request.session_id)
                )
                entry = prompt.strip_images([{"role": "user", "content": user_content}])[0]
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
            descriptions = await _wait_image_descriptions(description_task)
            if descriptions:
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
            if focus_instruction:
                # 共享组已经决定要在这里发言，指示只给方向，正文仍由本聊天的回复模型生成
                messages.append({"role": "user", "content": f"[Focus 共享组指示]\n{focus_instruction}"})
            if trace is not None:
                trace["budget"]["memory_chars"] = len(memory_block)
                trace["budget"]["summary_chars"] = len(summary)

            allowed_names, tools = await generator.allowed_tools(
                runtime, proactive=proactive, session_id=request.session_id
            )
            if trace is not None:
                trace["budget"]["tools"] = len(allowed_names)
            planner_cfg = plan_flow.planner_config() if focus_instruction is None else {}
            planner_expected = planner_cfg.get("mode") == "complex"
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
            plan = None
            if focus_instruction is None:
                plan = await self._plan_with_steering(
                    context, tools, history_len=len(history), memory_chars=len(memory_block)
                )
                if planner_expected and not self._alive(request, epoch):
                    await self._mark_turn_aborted(request, "规划期间被新触发取代")
                    return ChatResponse(text="", epoch=epoch)
                if plan is None and proactive and planner_expected:
                    await history_module.end_turn_without_reply(request, provider_name)
                    return ChatResponse(text="", epoch=epoch, metadata={"plan_action": "no_action"})
                if plan is not None:
                    outcome = await plan_flow.dispatch_plan(self, context, plan)
                    if outcome.finished:
                        return ChatResponse(text="", epoch=epoch, metadata={"plan_action": plan.action})
                    if outcome.pause:
                        return ChatResponse(
                            text="",
                            epoch=epoch,
                            metadata={"plan_action": plan.action, "wait_seconds": outcome.pause},
                        )
                    if outcome.messages is not None:
                        context.messages = outcome.messages
                if plan is not None and plan.action == "emoji_reply":
                    prompt.append_note(context.messages, "[本轮只回一个自然的表情或很短的文字，不要展开或解释。]")
            messages = context.messages
            plan_action = plan.action if plan is not None else ""
            if trace is not None:
                trace["plan_action"] = plan_action

            if style_note := prompt.temporary_style_note(behavior):
                prompt.append_note(messages, style_note)
            if (
                plan is not None
                and plan.reply_reference
                and plan.action in {"text_reply", "emoji_reply"}
                and planner_cfg.get("passive_replyer")
            ):
                # 被动式 replyer：规划正文只当作发言指导，正文仍由回复模型生成
                messages.append({"role": "user", "content": f"[发言指导]\n{plan.reply_reference}"})
            response = await generator.generate(
                self, request, runtime, provider, messages, tools, allowed_names, epoch, limit
            )
            post_cfg = chat_cfg.get("response_post_process") or {}
            splitter_cfg = chat_cfg.get("response_splitter")
            # 断句可单独走快速模型，未配置时沿用回复模型
            splitter_scope = (
                fallback_provider("fast_model", request.session_id)
                if routed_task("fast_model", "reply") == "fast_model"
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
            if plan_action:
                result.metadata["plan_action"] = plan_action
            if plan is not None and plan.emoji_hash:
                result.metadata["attach_emoji"] = plan.emoji_hash
            if parts:
                result.metadata["reply_parts"] = parts
            if item_ids is not None:
                result.metadata["turn_item_ids"] = item_ids
            return result
        finally:
            if description_task is not None:
                if not description_task.done():
                    description_task.cancel()
                await asyncio.gather(description_task, return_exceptions=True)


chat_service = ChatService()
