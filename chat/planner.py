"""一轮对话的动作规划：结构化动作、解析与样本落库。"""

from __future__ import annotations

import re
import json
from typing import Any
from dataclasses import field, dataclass

from nonebot import logger

from .types import ChatRequest
from ..config import get_manager as get_config_manager
from .memory.models import ChatPlannerSample
from .memory.repository import chat_repository

_WAIT_MIN = 30
_WAIT_MAX = 120
_REPLY_ACTIONS = frozenset({"text_reply", "emoji_reply", "fetch_knowledge", "tool"})
_SIMPLE_ACTIONS = frozenset({"no_reply", "text_reply", "emoji_reply", "wait", "end_conversation"})
_QUESTION = re.compile(r"[?？]|吗|呢|怎么|为什么|如何|多少|哪|谁|什么|是不是|能不能")
_PLACEHOLDER = re.compile(r"^\s*(?:\[(?:图片|表情包|消息类型|语音|视频)[^\]]*\]\s*)+$")
PLANNER_DEFAULTS: dict[str, Any] = {"enabled": False, "mode": "simple", "max_rounds": 1, "wait_seconds": 60}


def planner_config() -> dict[str, Any]:
    """返回带默认值的规划配置；每次使用时重新读取以支持热更新。"""
    try:
        value = get_config_manager().get_system()["chat"].get("planner")
    except Exception:
        value = None
    merged = dict(PLANNER_DEFAULTS)
    if isinstance(value, dict):
        merged.update(value)
    return merged


def planner_model_name() -> str:
    """规划模型路由；未配置时用 Provider 默认模型。"""
    try:
        routing = get_config_manager().get_system()["chat"].get("model_routing") or {}
        return str(routing.get("planner") or "")
    except Exception:
        return ""


@dataclass(frozen=True)
class Plan:
    """规划器选定的首轮动作。"""

    action: str
    tool: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    wait_seconds: int = 0

    @property
    def label(self) -> int:
        """首轮决定回复记 1，其余记 0。"""
        return 1 if self.action in _REPLY_ACTIONS else 0


def build_prompt(messages: list[dict[str, Any]], tools: list[str], hint: str = "") -> list[dict[str, Any]]:
    """在原对话上下文后追加规划指令；规划器只输出动作 JSON。"""
    tool_line = "、".join(tools) if tools else "无"
    prefix = f"当前状态与历史行为：\n{hint}\n\n" if hint else ""
    note = (
        f"{prefix}"
        "你是对话规划器。根据上面的上下文，决定这一轮做什么；不要写回复正文。\n"
        "只输出一个 JSON 对象，不要解释。\n"
        "可选 action：text_reply（正常回复）、no_reply（保持安静）、emoji_reply（只回一个表情）、"
        "wait（等一会儿再回复）、end_conversation（结束当前对话）、fetch_knowledge（查知识库）、"
        "tool:<工具名>（先调用工具再回复）。\n"
        f"可用工具：{tool_line}。\n"
        '示例：{"action":"text_reply"}、{"action":"no_reply"}、'
        '{"action":"fetch_knowledge","query":"..."}、{"action":"tool:web_search","arguments":{"query":"..."}}。\n'
        "被明确 @、私聊或称呼时通常应当回复；只有明显不需要接话时才选 no_reply 或 end_conversation。"
        "请只输出这一轮的规划 JSON。"
    )
    return [*messages, {"role": "user", "content": note}]


def _arguments(value: object) -> dict[str, Any]:
    """工具参数只接受对象；JSON 字符串解析失败时按空参数处理。"""
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def parse_plan(data: object, tools: list[str], *, default_wait: int = 60) -> Plan | None:
    """校验规划输出；未知动作或不可用工具返回 None，由调用方回退普通回复。"""
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            return None
    if not isinstance(data, dict):
        return None
    action = str(data.get("action") or "").strip().lower()
    if action.startswith("tool:"):
        tool = action.split(":", 1)[1].strip()
        if not tool or tool not in tools:
            return None
        return Plan("tool", tool=tool, arguments=_arguments(data.get("arguments")))
    if action == "fetch_knowledge":
        if "search_knowledge" not in tools:
            return None
        query = str(data.get("query") or "").strip()
        arguments = _arguments(data.get("arguments"))
        if query and not arguments:
            arguments = {"query": query}
        return Plan("fetch_knowledge", tool="search_knowledge", arguments=arguments)
    if action == "wait":
        try:
            seconds = int(data.get("wait_seconds") or default_wait)
        except (TypeError, ValueError):
            seconds = default_wait
        return Plan("wait", wait_seconds=max(_WAIT_MIN, min(_WAIT_MAX, seconds)))
    if action in _SIMPLE_ACTIONS:
        return Plan(action)
    return None


async def request_plan(
    provider: Any,
    messages: list[dict[str, Any]],
    tools: list[str],
    *,
    model: str = "",
    default_wait: int = 60,
    hint: str = "",
) -> Plan | None:
    """调用规划模型；格式错误重试一次，仍失败返回 None。"""
    prompt = build_prompt(messages, tools, hint)
    schema: dict[str, Any] = {"type": "object", "properties": {"action": {"type": "string"}}}
    for attempt in range(2):
        try:
            data = await provider.chat_json(prompt, schema, model=model or None)
        except Exception as exc:
            logger.opt(exception=True).debug(f"[ChatPlanner] 规划调用失败（第 {attempt + 1} 次）: {type(exc).__name__}")
            continue
        plan = parse_plan(data, tools, default_wait=default_wait)
        if plan is not None:
            return plan
    return None


def plan_features(
    request: ChatRequest,
    *,
    history_len: int = 0,
    memory_chars: int = 0,
) -> dict[str, Any]:
    """记录可用于门控校准的轻量特征；不做检索。"""
    text = str(request.text or "")
    placeholders = bool(_PLACEHOLDER.match(text)) if text else bool(request.images)
    return {
        "trigger_kind": request.trigger_kind,
        "batched": bool(request.batched),
        "message_count": max(1, len(request.source_message_ids)),
        "authors": len(set(request.source_author_ids or [request.user_id])),
        "text_chars": len(text),
        "mentions": len(request.mentions),
        "has_question": bool(_QUESTION.search(text)),
        "placeholder_only": placeholders,
        "has_image": bool(request.images),
        "history_len": int(history_len),
        "memory_chars": int(memory_chars),
    }


async def record_plan_sample(
    request: ChatRequest,
    plan: Plan,
    features: dict[str, Any],
    *,
    provider: str = "",
    model: str = "",
) -> None:
    """每次成功规划写一条样本；失败只降级记录，不影响本轮。"""
    try:
        await chat_repository.save_planner_sample(
            ChatPlannerSample(
                tenant=request.session_id,
                turn_id=request.turn_id,
                trigger_kind=request.trigger_kind,
                action=plan.action,
                label=plan.label,
                features=json.dumps(features, ensure_ascii=False),
                provider=provider,
                model=model,
            )
        )
    except Exception:
        logger.opt(exception=True).debug("[ChatPlanner] 规划样本落库失败")
