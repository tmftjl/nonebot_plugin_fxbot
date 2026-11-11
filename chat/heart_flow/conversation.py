"""会话级 Planner 工具循环，适配 MaiBot 062d45c 的 src/maisaka/reasoning_engine.py。"""

from __future__ import annotations

import json
import asyncio
from time import perf_counter
from typing import Any
from datetime import datetime
from contextlib import suppress
from dataclasses import replace

from nonebot import logger

from . import plan_flow, emoji_tools
from ..emoji import emoji_config
from ..hooks import hook_context
from ..tools import ToolContext, execute_tool
from ..trace import turn_trace
from ..types import ChatResponse, speaker_label
from ..stages import execute_stage
from ..planner import Plan, plan_features, planner_config, record_plan_sample
from ...utils.tz import now
from ..providers import LLMResponse
from .attachments import tool_media, resolve_pictures
from ..tools.result import prepare_tool_result_content
from ..memory.config import expression_vector_config
from ..work_schedule import can_reply
from ..context_summary import planner_snapshot
from ..tools.discovery import sync_discovered, deferred_reminder
from ..behavior.mentions import reply_at_enabled
from ..memory.repository import chat_repository
from ..providers.routing import fallback_provider
from ..replyer.generator import TOOL_TIMEOUT, allowed_tools
from ..message_receive.history import end_turn_without_reply

MAX_INTERNAL_ROUNDS = 16


def control_tools(passive: bool) -> list[dict[str, Any]]:
    """生成当前模式允许使用的控制工具。"""
    reply_options: dict[str, Any] = {
        "reply_style": {"type": "string", "enum": ["简短表达", "正常回复", "长回复"]},
        "attach_pic": {
            "type": "array",
            "description": "随回复附图，使用消息 msg_id + index（从 0 开始），或工具返回的 media_index。",
            "items": {
                "type": "object",
                "properties": {
                    "msg_id": {"type": "string"},
                    "media_index": {"type": "string"},
                    "index": {"type": "integer", "minimum": 0, "default": 0},
                },
            },
        },
    }
    if expression_vector_config().get("enabled"):
        reply_options["expression_intent"] = {
            "type": "object",
            "description": "为本次回复选择表达：贴合对象、回复动作、场景、语气、优先及避免的说法。",
            "properties": {
                key: {"type": "string"} for key in ("focus", "reply_act", "scene", "tone", "prefer", "avoid")
            },
        }
    if emoji_config().get("use_new_send_logic"):
        reply_options["attach_emoji"] = {
            "type": "integer",
            "minimum": 0,
            "description": "从 show_emoji_list 的拼图选择一个表情包，填写图片序号后随回复发送；0 或不填表示不发送。",
        }
    if reply_at_enabled():
        reply_options["attach_at"] = {
            "type": "array",
            "items": {"type": "string"},
            "description": "要 @ 的发言人的消息 ID",
        }
    definitions = [
        ("wait", "等待后重新查看聊天并思考。", {"seconds": {"type": "integer", "minimum": 0}}, ["seconds"]),
        ("stop", "结束本轮思考并等待新消息。", {}, []),
        ("send_emoji", "发送一个表情包来表达情绪，参与聊天。", {"msg_id": {"type": "string"}}, []),
        (
            "show_emoji_list",
            "查看随机抽取的表情包选择拼图。图片会进入你的上下文，不会发送给聊天对象。"
            "调用 reply 时将选中图片的序号填入 attach_emoji；历史拼图的序号也可使用。",
            {},
            [],
        ),
    ]
    if passive:
        definitions.append(("set_reply_options", "设置下一次回复的附加选项，不直接发言。", reply_options, []))
    if not passive:
        definitions.append(
            (
                "reply",
                "根据目标消息和发言指导生成并发送回复；正文由回复模型生成。",
                {"msg_id": {"type": "string"}, "reply_reference": {"type": "string"}, **reply_options},
                ["msg_id", "reply_reference"],
            )
        )
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {"type": "object", "properties": properties, "required": required},
            },
        }
        for name, description, properties, required in definitions
    ]


async def request_step(provider: Any, messages: list[dict[str, Any]], tools: list[dict[str, Any]], *, stream: bool):
    """请求一次规划；流式模式只消费完整响应，不将思考片段发送给用户。"""
    if not stream:
        return await provider.chat(messages=messages, tools=tools, temperature=0.7, max_tokens=16384)
    final = None
    chunks: list[str] = []
    stream_response = provider.chat_stream(messages=messages, tools=tools, temperature=0.7, max_tokens=16384)
    try:
        async for response in stream_response:
            if response.is_stream_chunk:
                chunks.append(response.content)
            else:
                final = response
    finally:
        await stream_response.aclose()
    return final if final is not None else LLMResponse(content="".join(chunks))


async def interruptible_step(service: Any, context: Any, provider: Any, messages: list, tools: list):
    """新消息到达时取消规划并重建上下文；清空会话时终止。"""
    request = context.request
    while service.is_current(request.session_id, context.epoch):
        if not can_reply(session_id=request.session_id):
            return None
        version = service.session_store.activity(request.session_id)
        request.metadata["passive_reply_target"] = next(
            (
                str(message["_source_ids"][-1])
                for message in reversed(service.session_store.get(request.session_id))
                if message.get("_source_ids") and not message.get("_planner_internal")
            ),
            request.message_id or request.dedupe_key,
        )
        steering = plan_flow.steering_enabled()
        task = asyncio.create_task(
            asyncio.wait_for(
                execute_stage(
                    "planner",
                    hook_context(request, phase="planner"),
                    lambda: request_step(provider, messages, tools, stream=steering),
                    rejected=LLMResponse(content=""),
                ),
                180,
            )
        )
        try:
            while not task.done():
                done, _ = await asyncio.wait({task}, timeout=0.1)
                if not service.is_current(request.session_id, context.epoch) or not can_reply(
                    session_id=request.session_id
                ):
                    return None
                if steering and service.session_store.activity(request.session_id) != version:
                    break
                if done:
                    return task.result()
            else:
                return task.result()
        finally:
            if not task.done():
                task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        rebuilt = await plan_flow.rebuild_messages(service, context)
        request.metadata.pop("planner_pending", None)
        messages[:] = [
            *rebuilt,
            *request.metadata.get("planner_instruction", []),
        ]
    return None


async def run(service: Any, context: Any) -> ChatResponse:
    """执行 Planner、工具、回复与等待循环，所有状态限定在当前会话。"""
    request = context.request
    passive = bool(planner_config().get("passive_replyer"))
    trace = request.metadata.setdefault("planner_trace", [])
    messages = list(context.messages)
    sent_any = bool(request.metadata.get("planner_sent"))
    snapshot = None
    instruction = (
        "需要信息或操作时调用工具；决定发言时直接输出指导正文，不附带工具调用；不发言用 stop，需要稍后判断用 wait。"
        if passive
        else "请你进行分析，是不调用工具，进行思考和等待\n还是使用工具搜集处理信息或使用reply回复"
    )
    reminder = plan_flow.wait_reminder(request.session_id)
    request.metadata["planner_instruction"] = [
        {"role": "user", "content": f"时间：{now():%Y-%m-%d %H:%M:%S}"},
        {"role": "user", "content": f"{instruction}\n{reminder}"},
    ]
    messages.extend(request.metadata["planner_instruction"])
    post_messages: list[dict[str, Any]] = []
    reply_states = request.metadata.setdefault("planner_reply_states", {})
    evidence = request.metadata.setdefault("planner_tool_evidence", {})

    def information_state() -> tuple[int, str]:
        """只用新入站活动和实质工具结果判断能否补充，模型改写指导不算新信息。"""
        return service.session_store.activity(request.session_id), json.dumps(
            evidence, sort_keys=True, ensure_ascii=False
        )

    def remember_internal(message: dict[str, Any]) -> None:
        """按真实执行顺序保存 Planner 协议，不能在所有聊天消息之后补写。"""
        if service.is_current(request.session_id, context.epoch):
            service.session_store.append(
                request.session_id,
                {**message, "_planner_internal": True, "_logical_turn_id": request.turn_id},
                limit=context.limit,
            )

    async def finish_cycle() -> None:
        """每次内部循环处理上下文，不等整个长对话结束才裁切和生成状态摘要。"""
        if snapshot is None or not service.is_current(request.session_id, context.epoch):
            return
        service.session_store.process_cycle(request.session_id, context.limit)
        dropped = service.session_store.take_dropped(request.session_id)
        if dropped:
            from ..context_summary import compact_messages

            await compact_messages(
                request.session_id,
                dropped,
                planner_request=snapshot,
                recent_messages=service.session_store.get(request.session_id),
            )

    async def reply(arguments: dict[str, Any]) -> tuple[dict[str, Any], ChatResponse | None]:
        """回复目标只能引用当前会话消息，发送结果回到 Planner。"""
        nonlocal sent_any
        reference = arguments.get("reply_reference")
        target = str(arguments.get("msg_id") or "")
        target_ids = request.source_message_ids or [request.message_id or request.dedupe_key]
        known_ids = set(target_ids)
        for message in service.session_store.get(request.session_id):
            known_ids.update(str(value) for value in message.get("_source_ids", []))
        if target not in known_ids or not isinstance(reference, str) or not reference.strip():
            return {"ok": False, "error": "请提供本会话有效的 msg_id 和非空 reply_reference"}, None
        current_information = information_state()
        if reply_states.get(target) == current_information:
            return {"ok": False, "duplicate_reply": True, "content": "该消息已回复且没有新信息，结束本轮"}, None
        authors = {str(message_id): request.user_id for message_id in target_ids}
        source_senders = request.metadata.get("source_senders", {})
        authors.update({str(identity): str(sender["user_id"]) for identity, sender in source_senders.items()})
        for message in service.session_store.get(request.session_id):
            if author := message.get("_sender_id"):
                authors.update({str(message_id): str(author) for message_id in message.get("_source_ids", [])})
        attached = arguments.get("attach_at") or []
        if not isinstance(attached, list) or any(str(message_id) not in authors for message_id in attached):
            return {"ok": False, "error": "attach_at 必须引用当前会话中发言人可确认的消息"}, None
        pictures = await resolve_pictures(service, request, arguments.get("attach_pic"), [*trace, *post_messages])
        selected = source_senders.get(target, {})
        sender_id = authors.get(target, request.user_id)
        sender_name = str(
            selected.get("user_name") or (request.user_name if sender_id == request.user_id else sender_id)
        )
        selected_text = str(selected.get("text") or request.text)
        selected_time = request.occurred_at
        selected_quote = (
            selected.get("quote_snapshot")
            if selected
            else request.metadata.get("quote_snapshot")
            if target == request.message_id
            else None
        )
        selected_role = (
            selected.get("role")
            if selected
            else request.metadata.get("sender_role")
            if target == request.message_id
            else None
        )
        target_quotes: list[str] = []
        target_at_bot = bool(request.metadata.get("has_at_bot")) if target == request.message_id else False
        for message in service.session_store.get(request.session_id):
            if target in message.get("_source_ids", []):
                sender_id = str(message.get("_sender_id") or sender_id)
                sender_name = str(message.get("_sender_name") or sender_name)
                prefix = f"{speaker_label(sender_id, sender_name)}: "
                selected_text = str(message.get("content") or "").removeprefix(prefix)
                target_quotes = list(message.get("_quote_ids", []))
                target_at_bot = bool(message.get("_at_bot"))
                selected_role = message.get("_sender_role")
                selected_quote = message.get("_quote_snapshot")
                if timestamp := message.get("_timestamp"):
                    selected_time = datetime.fromisoformat(timestamp)
        if target != (request.message_id or request.dedupe_key):
            row = await chat_repository.message_by_identity(request.session_id, target)
            if row is not None:
                sender_id, sender_name = row.sender_id, row.sender_name
                prefix = f"{speaker_label(sender_id, sender_name)}: "
                selected_text = row.content.removeprefix(prefix)
                selected_time = row.occurred_at or row.ingested_at
                source_context = json.loads(row.payload or "{}").get("context", {})
                selected_role = source_context.get("_sender_role")
                selected_quote = source_context.get("_quote_snapshot")
                target_quotes = source_context.get("_quote_ids") or (
                    [row.reply_to_message_id] if row.reply_to_message_id else []
                )
        previous_reply = next(
            (
                str(message.get("content") or "")
                for message in reversed(service.session_store.get(request.session_id))
                if message.get("_self_message") and target in message.get("_quote_ids", [])
            ),
            "",
        )
        if previous_reply:
            reference = (
                f"{reference}\n你刚刚已经回复过这条消息，你刚刚的发言是：“{' '.join(previous_reply.split())}”\n"
                "你现在想再次回复这条消息，进行补充，注意请不要和之前你的发言重复。"
            )
        attempt = int(request.metadata.get("planner_reply_attempts") or 0) + 1
        request.metadata["planner_reply_attempts"] = attempt
        reply_request = replace(
            request,
            turn_id=request.turn_id,
            user_id=sender_id,
            user_name=sender_name,
            text=selected_text,
            message_id=target,
            occurred_at=selected_time,
            source_message_ids=[target],
            source_author_ids=[sender_id],
            images=request.images if target == request.message_id else [],
            mentions=request.mentions if target == request.message_id else [],
            quoted_text=request.quoted_text if target == request.message_id else "",
            reply_to_message_id=target,
            metadata={
                **request.metadata,
                "history_recorded": True,
                "planner_reply": True,
                "planner_parent_turn_id": request.turn_id,
                "turn_attempt": attempt,
                "reply_target_id": target,
                "target_quote_ids": target_quotes,
                "has_at_bot": target_at_bot,
                "sender_role": selected_role,
                "quote_snapshot": selected_quote,
                "requested_reply_style": str(arguments.get("reply_style") or ""),
                "expression_intent": arguments.get("expression_intent")
                if expression_vector_config().get("enabled")
                else None,
                "planner_trace": [],
                "reply_at_users": list(dict.fromkeys(authors[str(message_id)] for message_id in attached)),
            },
        )
        response = await service._process_locked(reply_request, context.runtime, focus_instruction=reference)
        if pictures:
            response.metadata["attach_pictures"] = pictures
        attachment = arguments.get("attach_emoji")
        if attachment:
            digest = emoji_tools.selected_hash(
                request, attachment, [*service.session_store.get(request.session_id), *trace, *post_messages]
            )
            if digest:
                response.metadata["attach_emoji"] = digest
        if getattr(context.runtime, "matcher", None) is None:
            await end_turn_without_reply(request, context.provider_name)
            return {"ok": True}, response
        sent = await service._send_generated(reply_request, response, context.runtime)
        sent_any = sent_any or sent
        request.metadata["planner_sent"] = sent_any
        if sent:
            reply_states[target] = current_information
        return {"ok": sent, "content": response.text if sent else "回复未成功发送"}, None

    async with fallback_provider("planner", request.session_id) as provider:
        for round_index in range(MAX_INTERNAL_ROUNDS):
            if not service.is_current(request.session_id, context.epoch) or not can_reply(
                session_id=request.session_id
            ):
                break
            await finish_cycle()
            if request.metadata.pop("planner_pending", False) or round_index > 0:
                rebuilt = await plan_flow.rebuild_messages(service, context)
                messages[:] = [*rebuilt, *request.metadata["planner_instruction"]]
            sync_discovered(request.session_id, messages)
            names, tools = await allowed_tools(context.runtime, session_id=request.session_id)
            controls = control_tools(passive)
            runtime_tools = getattr(context.runtime, "allowed_tools", None)
            if runtime_tools is not None:
                controls = [item for item in controls if item["function"]["name"] in runtime_tools]
            tools = [*controls, *tools]
            request_messages = list(messages)
            if reminder := deferred_reminder(names, request.session_id):
                request_messages.append({"role": "user", "content": reminder})
            started = perf_counter()
            failure = ""
            called: list[str] = []
            try:
                response = await interruptible_step(service, context, provider, request_messages, tools)
                tool_calls = getattr(response, "tool_calls", None) or []
                called = [str((call.get("function") or {}).get("name") or "") for call in tool_calls]
            except Exception:
                failure = "规划请求失败"
                raise
            finally:
                if state := turn_trace(request):
                    state.add_stage(
                        "planner",
                        "Planner 决策",
                        int((perf_counter() - started) * 1000),
                        provider=str(getattr(provider, "provider_id", "")),
                        error=failure,
                        tools=called,
                    )
            if response is None:
                break
            snapshot = planner_snapshot(provider, request_messages, tools)
            messages[:] = request_messages
            if reminder and messages and messages[-1].get("content") == reminder:
                messages.pop()
            if not request.metadata.get("planner_sampled"):
                names_called = called
                action = (
                    "text_reply"
                    if "reply" in names_called or (passive and response.content and not names_called)
                    else "wait"
                    if "wait" in names_called
                    else "tool"
                    if any(name != "stop" for name in names_called)
                    else "no_reply"
                )
                await record_plan_sample(
                    request,
                    Plan(action),
                    plan_features(request, history_len=len(context.messages)),
                    provider=str(getattr(provider, "provider_id", "")),
                    model=str(getattr(provider, "get_model", lambda: "")()),
                )
                request.metadata["planner_sampled"] = True
            assistant = response.to_message_dict()
            messages.append(assistant)
            trace.append(assistant)
            remember_internal(assistant)
            if not response.tool_calls:
                if passive and response.content.strip():
                    options = request.metadata.pop("planner_reply_options", {})
                    target = str(request.metadata["passive_reply_target"])
                    _, generated = await reply({**options, "msg_id": target, "reply_reference": response.content})
                    if generated is not None:
                        await finish_cycle()
                        return generated
                break
            pause: float | None = None
            stopped = False
            post_messages: list[dict[str, Any]] = []
            for call in response.tool_calls:
                function = call.get("function") or {}
                name = str(function.get("name") or "")
                generated = None
                try:
                    raw_arguments = function.get("arguments") or "{}"
                    arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
                    if not isinstance(arguments, dict):
                        raise ValueError("工具参数必须为对象")
                    if (
                        stopped
                        or pause is not None
                        or not service.is_current(request.session_id, context.epoch)
                        or not can_reply(session_id=request.session_id)
                    ):
                        result = {"ok": False, "error": "本轮已暂停或结束，工具未执行"}
                    elif runtime_tools is not None and name not in runtime_tools:
                        result = {"ok": False, "error": "当前聊天渠道不支持该工具"}
                    elif name == "reply" and not passive:
                        result, generated = await reply(arguments)
                        stopped = bool(result.get("duplicate_reply"))
                    elif name == "stop":
                        request.metadata.pop("planner_reply_options", None)
                        stopped = True
                        result = {"ok": True, "content": "已结束本轮思考，等待新消息"}
                    elif name == "set_reply_options" and passive:
                        request.metadata["planner_reply_options"] = arguments
                        result = {"ok": True, "content": "已设置下次回复选项"}
                    elif name == "show_emoji_list":
                        result, additions = await emoji_tools.show_candidates(request)
                        post_messages.extend(additions)
                    elif name == "send_emoji":
                        result = await emoji_tools.send_selected(
                            service,
                            context,
                            response.content or request.text,
                            target_id=str(arguments.get("msg_id") or ""),
                        )
                        sent_any = sent_any or bool(result.get("ok"))
                        request.metadata["planner_sent"] = sent_any
                    elif name == "wait":
                        seconds = arguments.get("seconds")
                        if isinstance(seconds, bool) or not isinstance(seconds, int) or seconds < 0:
                            raise ValueError("seconds 必须是非负整数")
                        request.metadata.pop("planner_reply_options", None)
                        pause = seconds
                        result = {"ok": True, "content": f"等待 {pause} 秒后重新思考"}
                    else:
                        tool_context = ToolContext(
                            user_id=request.user_id,
                            group_id=request.group_id,
                            session_id=request.session_id,
                            platform=request.platform,
                            metadata={**request.metadata, "allowed_tools": sorted(names)},
                        )
                        result = await execute_tool(name, arguments, tool_context, context.runtime, allowed_names=names)
                        if name != "tool_search":
                            key = name + ":" + json.dumps(arguments, sort_keys=True, ensure_ascii=False)
                            evidence[key] = json.dumps(result, sort_keys=True, ensure_ascii=False, default=str)
                        result, additions = tool_media(result, str(call.get("id") or name))
                        post_messages.extend(additions)
                except (ValueError, TypeError):
                    result = {"ok": False, "error": "工具参数无效"}
                except Exception:
                    logger.opt(exception=True).warning("[ChatPlanner] 工具执行失败")
                    result = {"ok": False, "error": "工具执行失败"}
                output = {
                    "role": "tool",
                    "tool_call_id": call.get("id"),
                    "name": name,
                    "content": await prepare_tool_result_content(
                        result,
                        session_id=request.session_id,
                        tool_name=name,
                        arguments=function.get("arguments") or "{}",
                        timeout=TOOL_TIMEOUT,
                    ),
                }
                messages.append(output)
                trace.append(output)
                remember_internal(output)
                if generated is not None:
                    await finish_cycle()
                    return generated
            messages.extend(post_messages)
            trace.extend(post_messages)
            for message in post_messages:
                remember_internal(message)
            if stopped:
                break
            if pause is not None:
                plan_flow.note_plan(request.session_id, "wait")
                if pause > 0:
                    await finish_cycle()
                    request.activity_seq = service.session_store.activity(request.session_id)
                    return ChatResponse(text="", epoch=context.epoch, metadata={"wait_seconds": pause})
            else:
                plan_flow.note_plan(request.session_id, "tool")
    request.metadata.pop("planner_trace", None)
    request.metadata.pop("planner_reply_options", None)
    request.metadata.pop("planner_instruction", None)
    plan_flow.note_plan(request.session_id, "stop")
    await finish_cycle()
    await end_turn_without_reply(request, context.provider_name)
    return ChatResponse(text="", epoch=context.epoch, metadata={"planner_sent": sent_any})
