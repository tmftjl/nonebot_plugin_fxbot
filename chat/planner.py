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
_REPLY_ACTIONS = frozenset({"text_reply", "emoji_reply", "tool"})
_SIMPLE_ACTIONS = frozenset({"no_reply", "text_reply", "emoji_reply", "wait", "end_conversation"})
_QUESTION = re.compile(r"[?？]|吗|呢|怎么|为什么|如何|多少|哪|谁|什么|是不是|能不能")
_PLACEHOLDER = re.compile(r"^\s*(?:\[(?:图片|表情包|消息类型|语音|视频)[^\]]*\]\s*)+$")
PLANNER_DEFAULTS: dict[str, Any] = {
    "mode": "simple",
    "wait_seconds": 60,
    "context_summary_max_chars": 6000,
}


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


@dataclass(frozen=True)
class Plan:
    """规划器选定的首轮动作。"""

    action: str
    tool: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    wait_seconds: int = 0
    emoji_hash: str = ""
    reply_reference: str = ""

    @property
    def label(self) -> int:
        """首轮决定回复记 1，其余记 0。"""
        return 1 if self.action in _REPLY_ACTIONS else 0


def tool_names(tools: list[Any]) -> list[str]:
    """取工具名；调用方可以传名称，也可以传完整定义。"""
    names: list[str] = []
    for item in tools:
        if isinstance(item, dict):
            function = item.get("function")
            name = str(function.get("name") or "") if isinstance(function, dict) else ""
        else:
            name = str(item)
        if name:
            names.append(name)
    return names


def tool_line(tools: list[Any]) -> str:
    """工具清单带用途与参数，避免规划器选对工具却猜错参数。"""
    lines: list[str] = []
    for item in tools:
        if not isinstance(item, dict):
            lines.append(f"- {item}")
            continue
        function = item.get("function")
        if not isinstance(function, dict) or not function.get("name"):
            continue
        parameters = function.get("parameters")
        parameters = parameters if isinstance(parameters, dict) else {}
        properties = parameters.get("properties")
        properties = properties if isinstance(properties, dict) else {}
        required = {str(name) for name in parameters.get("required") or ()}
        fields = []
        for name, spec in properties.items():
            kind = str(spec.get("type") or "") if isinstance(spec, dict) else ""
            fields.append(f"{name}({kind or 'any'}){'*' if name in required else ''}")
        line = f"- {function['name']}: {str(function.get('description') or '').strip()}"
        lines.append(f"{line}；参数 {', '.join(fields)}" if fields else line)
    return "\n".join(lines) if lines else "无"


def build_prompt(
    messages: list[dict[str, Any]],
    tools: list[Any],
    hint: str = "",
    emoji_image: str | None = None,
    *,
    passive: bool = False,
) -> list[dict[str, Any]]:
    """在原对话上下文后追加规划指令；规划器只输出动作 JSON。"""
    tools_text = tool_line(tools)
    prefix = f"当前状态与历史行为：\n{hint}\n\n" if hint else ""
    emoji_note = ""
    emoji_example = ""
    if emoji_image:
        emoji_note = "需要发送表情包时，从附图的表情包拼图里挑一个，把序号填进 emoji_index；没有合适的就省略。\n"
        emoji_example = '、{"action":"text_reply","emoji_index":2}'
    passive_note = (
        "被动式回复：决定发言时在 reply_reference 写出发言指导，说明这一轮想表达什么、用什么语气；"
        "不要写最终发送的正文。不发言或调用工具时省略。\n"
        if passive
        else ""
    )
    passive_example = '、{"action":"text_reply","reply_reference":"顺着爬山话题接一句，语气轻松"}' if passive else ""
    note = (
        f"{prefix}"
        "你是对话规划器。根据上面的上下文，决定这一轮做什么；不要写回复正文。\n"
        "只输出一个 JSON 对象，不要解释。\n"
        "可选 action：text_reply（正常回复）、no_reply（保持安静）、emoji_reply（只回一个表情）、"
        "wait（等一会儿再回复）、end_conversation（结束当前对话）、"
        "tool:<工具名>（先调用工具再回复）。\n"
        f"可用工具（参数带 * 为必填）：\n{tools_text}\n"
        f"{emoji_note}"
        f"{passive_note}"
        '示例：{"action":"text_reply"}、{"action":"no_reply"}、'
        '{"action":"tool:web_search","arguments":{"query":"..."}}'
        f"{emoji_example}{passive_example}。\n"
        "被明确 @、私聊或称呼时通常应当回复；只有明显不需要接话时才选 no_reply 或 end_conversation。"
        "请只输出这一轮的规划 JSON。"
    )
    last: dict[str, Any] = {"role": "user", "content": note}
    if emoji_image:
        last["content"] = [
            {"type": "text", "text": note},
            {"type": "image_url", "image_url": {"url": emoji_image}},
        ]
    return [*messages, last]


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


def _emoji_hash(data: dict[str, Any], hashes: list[str] | None) -> str:
    """把规划器填写的候选编号映射成表情哈希；越界或未提供候选时留空。"""
    if not hashes:
        return ""
    try:
        index = int(data.get("emoji_index") or 0)
    except (TypeError, ValueError):
        return ""
    return hashes[index - 1] if 1 <= index <= len(hashes) else ""


def parse_plan(
    data: object,
    tools: list[str],
    *,
    default_wait: int = 60,
    emoji_hashes: list[str] | None = None,
) -> Plan | None:
    """校验规划输出；未知动作或不可用工具返回 None，由调用方回退普通回复。"""
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            return None
    if not isinstance(data, dict):
        return None
    action = str(data.get("action") or "").strip().lower()
    emoji_hash = _emoji_hash(data, emoji_hashes)
    reference = str(data.get("reply_reference") or "").strip()
    if action.startswith("tool:"):
        tool = action.split(":", 1)[1].strip()
        if not tool or tool not in tools:
            return None
        return Plan("tool", tool=tool, arguments=_arguments(data.get("arguments")), emoji_hash=emoji_hash)
    if action == "wait":
        try:
            seconds = int(data.get("wait_seconds") or default_wait)
        except (TypeError, ValueError):
            seconds = default_wait
        return Plan("wait", wait_seconds=max(_WAIT_MIN, min(_WAIT_MAX, seconds)), emoji_hash=emoji_hash)
    if action in _SIMPLE_ACTIONS:
        return Plan(action, emoji_hash=emoji_hash, reply_reference=reference)
    return None


async def request_plan(
    provider: Any,
    messages: list[dict[str, Any]],
    tools: list[Any],
    *,
    model: str = "",
    default_wait: int = 60,
    hint: str = "",
    emoji_image: str | None = None,
    emoji_hashes: list[str] | None = None,
) -> Plan | None:
    """调用规划模型；格式错误重试一次，仍失败返回 None。"""
    passive = bool(planner_config().get("passive_replyer"))
    prompt = build_prompt(messages, tools, hint, emoji_image, passive=passive)
    names = tool_names(tools)
    schema: dict[str, Any] = {"type": "object", "properties": {"action": {"type": "string"}}}
    for attempt in range(2):
        try:
            data = await provider.chat_json(prompt, schema, model=model or None)
        except Exception as exc:
            logger.opt(exception=True).debug(f"[ChatPlanner] 规划调用失败（第 {attempt + 1} 次）: {type(exc).__name__}")
            if emoji_image and attempt == 0:
                # 规划模型不支持图片时退回纯文本规划，本轮不挑表情
                emoji_image, emoji_hashes = None, None
                prompt = build_prompt(messages, tools, hint, None, passive=passive)
            continue
        plan = parse_plan(data, names, default_wait=default_wait, emoji_hashes=emoji_hashes)
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
