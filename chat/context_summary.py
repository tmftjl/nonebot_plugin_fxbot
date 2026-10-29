"""上下文压缩：把被历史窗口挤掉的旧消息压缩成常驻状态摘要。

对齐 MaiBot（`src/maisaka/context/compaction.py`）：摘要不是逐条日志，而是接续聊天用的
状态笔记，随 Planner 与回复请求一起发送；压缩失败时保留上一份摘要。
"""

from __future__ import annotations

import json
import asyncio
from copy import deepcopy
from typing import Any
from dataclasses import dataclass

from nonebot import logger

from .planner import planner_config
from .memory.prompts import learning_prompt
from .providers.usage import track_model_task_usage
from .providers.manager import provider_manager

SUMMARY_HEADER = "[上下文状态摘要-内部参考，不是用户发言]"
SUMMARY_MAX_CHARS_DEFAULT = 6000
SUMMARY_MIN_CHARS = 1000
SUMMARY_MAX_CHARS = 8000
STATE_LABELS = {
    "topics": "当前话题",
    "background": "关键背景",
    "progress": "进展与结论",
    "pending": "未完事项",
    "agreements": "对话约定",
}

_summaries: dict[str, str] = {}
_summary_generations: dict[str, int] = {}
# 已压缩进摘要的消息来源；撤回时据此判断摘要里是否还有被撤回的内容
_summary_sources: dict[str, set[str]] = {}


@dataclass(frozen=True)
class PlannerRequestSnapshot:
    """成功 Planner 请求的独立副本，只驻留当前规划循环内存。"""

    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]]
    provider_id: str
    provider_config: dict[str, Any]
    model: str
    temperature: float = 0.7
    max_tokens: int = 16384


def planner_snapshot(
    provider: Any, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
) -> PlannerRequestSnapshot:
    """在成功响应后保存实际 Provider、消息、工具及任务参数。"""
    getter = getattr(provider, "get_model", None)
    return PlannerRequestSnapshot(
        messages=deepcopy(messages),
        tools=deepcopy(tools),
        provider_id=str(getattr(provider, "provider_id", "") or ""),
        provider_config=deepcopy(getattr(provider, "config", {}) or {}),
        model=str(getter() or "") if callable(getter) else "",
    )


def _message_signature(message: dict[str, Any]) -> str:
    """按模型协议字段识别请求快照已经覆盖的资料。"""
    return json.dumps(
        {key: message[key] for key in ("role", "content", "tool_call_id", "tool_calls") if key in message},
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )


def build_snapshot_messages(
    snapshot: PlannerRequestSnapshot,
    previous: str,
    messages: list[dict[str, Any]],
    recent_messages: list[dict[str, Any]],
    max_chars: int,
    source_context: str = "",
) -> list[dict[str, Any]]:
    """保留完整请求前缀，仅追加未覆盖资料和上游压缩提示词。"""
    seen = {_message_signature(message) for message in snapshot.messages}
    delta = []
    if previous and not any(previous in str(message.get("content") or "") for message in snapshot.messages):
        delta.append(previous)
    for message in [*messages, *recent_messages]:
        signature = _message_signature(message)
        if signature not in seen:
            seen.add(signature)
            if text := _message_text(message):
                delta.append(text)
    parts = ["<additional_conversation_data>", *delta, "</additional_conversation_data>"]
    if source_context:
        parts.extend(["<conversation_sources>", source_context, "</conversation_sources>"])
    parts.append(
        learning_prompt(
            "context_compaction_focus" if source_context else "context_compaction", max_summary_chars=max_chars
        )
    )
    return [*deepcopy(snapshot.messages), {"role": "user", "content": "\n".join(parts)}]


def summary_max_chars() -> int:
    """读取状态摘要字符上限；非法值回退默认。"""
    value = planner_config().get("context_summary_max_chars", SUMMARY_MAX_CHARS_DEFAULT)
    if isinstance(value, bool) or not isinstance(value, int):
        return SUMMARY_MAX_CHARS_DEFAULT
    return min(max(value, SUMMARY_MIN_CHARS), SUMMARY_MAX_CHARS)


def current_summary(session_id: str) -> str:
    """返回会话当前状态摘要；没有时为空字符串。"""
    return _summaries.get(session_id, "")


def clear_summary(session_id: str) -> None:
    """清空会话摘要，供 /reset 等清空历史的路径调用。"""
    _summary_generations[session_id] = _summary_generations.get(session_id, 0) + 1
    _summaries.pop(session_id, None)
    _summary_sources.pop(session_id, None)


def forget_summarized(session_id: str, message_id: str) -> bool:
    """撤回的消息已压缩进摘要时作废整份摘要：摘要无法逐句剥离。"""
    if str(message_id) not in _summary_sources.get(session_id, ()):
        return False
    clear_summary(session_id)
    return True


def _message_text(message: dict[str, Any]) -> str:
    """把一条历史消息编码成压缩资料；工具调用只保留工具名和参数。"""
    role = str(message.get("role") or "")
    content = message.get("content")
    if isinstance(content, list):
        content = "".join(
            str(part.get("text") or "") for part in content if isinstance(part, dict) and part.get("type") == "text"
        )
    text = str(content or "").strip()
    calls = message.get("tool_calls")
    if calls:
        return "\n".join(
            json.dumps(
                {
                    "kind": "tool_call",
                    "call_id": call.get("id"),
                    "tool": call.get("function", {}).get("name"),
                    "args": call.get("function", {}).get("arguments"),
                },
                ensure_ascii=False,
            )
            for call in calls
        )
    if role == "tool":
        return json.dumps(
            {
                "kind": "tool_result",
                "call_id": message.get("tool_call_id"),
                "tool": message.get("name"),
                "content": text,
            },
            ensure_ascii=False,
        )
    if role not in {"user", "assistant"} or not text:
        return ""
    return f"[{role}] {text}"


def render_state(data: Any, max_chars: int) -> str:
    """校验模型返回的五类字段并渲染成文本；格式错误或超预算时抛出。"""
    if not isinstance(data, dict):
        raise ValueError("摘要不是 JSON 对象")
    rendered = []
    for key, label in STATE_LABELS.items():
        entries = data.get(key)
        if not isinstance(entries, list) or any(not isinstance(entry, str) for entry in entries):
            raise ValueError(f"摘要字段 {key} 必须是字符串数组")
        cleaned = list(dict.fromkeys(entry.strip() for entry in entries if entry.strip()))
        if cleaned:
            rendered.append(f"{label}:\n" + "\n".join(f"- {entry}" for entry in cleaned))
    text = "\n\n".join(rendered)
    if not text:
        raise ValueError("摘要为空")
    if len(text) > max_chars:
        raise ValueError(f"摘要超过字符预算: {len(text)}/{max_chars}")
    return text


def parse_state(text: str, max_chars: int) -> str:
    """沿用上游严格 JSON 对象，拒绝额外字段和非摘要内容。"""
    payload = json.loads(str(text or ""))
    if not isinstance(payload, dict) or set(payload) != set(STATE_LABELS):
        raise ValueError("摘要字段不符合上游结构")
    return render_state(payload, max_chars)


async def compact_messages(
    session_id: str,
    messages: list[dict[str, Any]],
    *,
    timeout: float = 180.0,
    planner_request: PlannerRequestSnapshot | None = None,
    recent_messages: list[dict[str, Any]] | None = None,
    source_context: str = "",
    is_valid: Any = None,
) -> str:
    """把一批被挤出的消息并入会话摘要；失败时返回上一份摘要。"""
    previous = current_summary(session_id)
    if not messages:
        return previous
    from .memory.trimmed import trimmed_learning

    trimmed_learning.collect(session_id, messages)
    if planner_request is None:
        return previous
    generation = _summary_generations.get(session_id, 0)
    max_chars = summary_max_chars()
    try:
        request_messages = await asyncio.to_thread(
            build_snapshot_messages,
            planner_request,
            previous,
            messages,
            recent_messages or [],
            max_chars,
            source_context,
        )
        async with (
            provider_manager.lease_snapshot(
                planner_request.provider_id, deepcopy(planner_request.provider_config)
            ) as provider,
            track_model_task_usage("planner", provider),
        ):
            response = await asyncio.wait_for(
                provider.chat(
                    messages=request_messages,
                    tools=deepcopy(planner_request.tools) or None,
                    model=planner_request.model or None,
                    temperature=planner_request.temperature,
                    max_tokens=planner_request.max_tokens,
                ),
                timeout=timeout,
            )
        if response.tool_calls:
            raise ValueError("摘要响应不允许包含工具调用")
        summary = parse_state(str(response.content or ""), max_chars)
    except Exception:
        if generation != _summary_generations.get(session_id, 0):
            return current_summary(session_id)
        logger.opt(exception=True).warning("[Chat] 上下文压缩失败，保留上一份摘要")
        return previous
    if generation != _summary_generations.get(session_id, 0) or (is_valid is not None and not is_valid()):
        return current_summary(session_id)
    _summaries[session_id] = summary
    tracked = _summary_sources.setdefault(session_id, set())
    for message in messages:
        values = message.get("_source_ids")
        if isinstance(values, (list, tuple, set)):
            tracked.update(str(value) for value in values)
    logger.debug(f"[Chat] 上下文压缩完成: session={session_id} 压缩={len(messages)} 摘要字符={len(summary)}")
    return summary
