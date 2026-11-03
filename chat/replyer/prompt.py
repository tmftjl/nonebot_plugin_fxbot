"""提示词组装：人格、场景、历史与本轮消息。

人格与场景的拼装方式对齐 MaiBot（commit 062d45c，`src/chat/replyer/maisaka_generator_base.py`），
按 FxBot 的单层消息结构改写。
"""

from __future__ import annotations

import random
from typing import Any
from datetime import datetime

from ..types import ChatRequest, speaker_label
from ..bindings import PERSONA, get_binding
from ..personas import DEFAULT_PERSONA, get_persona_text, compose_persona_prompt
from ...utils.tz import as_shanghai
from ..work_schedule import work_life_prompt
from ..group_identity import group_identity_prompt
from ..context_summary import SUMMARY_HEADER
from ..message_context import render_message, message_metadata
from ..message_receive.reactions import reaction_block

MAX_CURRENT_CHARS = 4000

# 注意力漂移提示块对齐 MaiBot（commit 062d45c，`src/maisaka/attention_drift.py`），只保留中文。
_DRIFT_LEVEL_RULES = {
    "subtle": "漂移档位：轻微漂移。只在最近消息里出现非常自然的触发点时，轻轻联想一句；大多数时候继续当前话题。",
    "active": "".join(
        (
            "漂移档位：活跃联想。可以主动抓住新鲜、好笑、反差强或熟悉的细节接话，",
            "但回复仍要清楚、短促、能被最近消息解释。",
        )
    ),
    "scattered": "".join(
        (
            "漂移档位：明显发散。你可以明显地被支线、关键词、熟人语气或反差点勾走，",
            "先接住那个点再回到正题；回复里允许出现一次可理解的突然拐弯。",
        )
    ),
    "wild": "".join(
        (
            "漂移档位：强烈跳跃。你可以先被最有趣的细节劫走一下，出现短促插话、突然联想或半路拐弯；",
            "但每轮最多一次明显跳跃，不能无视明确提问，最后要让人看得出你在接哪条消息。",
        )
    ),
}
_ANCHOR_POLICY_RULES = {
    "strict": "回钩策略：严格回钩。联想或短反应之后，要立刻回到当前正在聊的主题或被回复对象。",
    "balanced": "回钩策略：自然回钩。可以短暂沿着支线说一句，但通常要让结尾或主要意思回到当前聊天。",
    "loose": "回钩策略：宽松关联。可以保留更自由的相关联想，但不能凭空换话题，也不能无视明确提问。",
}
_REACTION_STYLE_RULES = {
    "reserved": "短反应风格：少量短反应。只有特别适合接话时，才用一句很短的反应开头。",
    "natural": "短反应风格：自然短反应。可以偶尔先用短句、吐槽或语气词接住话题，再继续正常回复。",
    "lively": "短反应风格：活泼短反应。更容易先用短促反应开头，但不要把回复拆得太碎，也不要每次都这样。",
}
_ATTENTION_DRIFT_HEADER = (
    "注意力漂移风格：\n"
    "- 你可以短暂被聊天里新鲜、好笑、反差强或熟悉的人和梗吸引，但每次漂移都必须能从最近消息里找到明确触发点。\n"
    "- 漂移不是单纯换话题，而是先抓一个突出的触发点，再用很短的拐弯制造“脑子突然亮了一下”的感觉。\n"
    "- 表现活跃联想，不要真的降效；不要为了显得分心而故意拖延、忽略任务或打散工具调用。\n"
    "- 不要医学化描述这种风格，不要自称 ADHD，也不要主动声明自己分心。"
)


def mentions_line(mentions: list[Any]) -> str:
    """把被 @ 的成员渲染成单独一行，让模型拿到可以交给工具的 ID。"""
    if not mentions:
        return ""
    return "[@成员: " + ", ".join(ref.render() for ref in mentions) + "]"


def append_note(messages: list[dict[str, Any]], note: str) -> None:
    """把补充要求并进最后一条消息，避免新增 system 消息覆盖人格。"""
    if not messages:
        messages.append({"role": "user", "content": note})
        return
    content = messages[-1].get("content")
    if isinstance(content, str):
        messages[-1]["content"] = f"{content}\n{note}"
    elif isinstance(content, list):
        content.append({"type": "text", "text": note})
    else:
        messages.append({"role": "user", "content": note})


def requested_reply_style(style: str) -> str:
    """采用 MaiBot 原版的本轮篇幅指示。"""
    return {
        "简短表达": "请简短的回复，允许句子残缺，奇怪表达，倒装，省略，符合口语习惯，符合省力随意回复习惯",
        "正常回复": "",
        "长回复": "可以针对问题做出较为详细的评论和说明",
    }.get(style.strip(), "")


def _public(message: dict[str, Any], *, bot_name: str = "") -> dict[str, Any]:
    """去掉只给运行时用的内部字段，历史条目里的来源标记不发给模型。"""
    if message.get("_timestamp") or message.get("_reference"):
        return render_message(message, bot_name=bot_name)
    public = {key: value for key, value in message.items() if not str(key).startswith("_")}
    source_ids = message.get("_source_ids")
    if source_ids and public.get("role") == "user":
        identity = "[msg_id: " + ", ".join(str(value) for value in source_ids) + "]"
        content = public.get("content")
        public["content"] = (
            f"{identity}\n{content}"
            if isinstance(content, str)
            else [{"type": "text", "text": identity}, *(content or [])]
        )
    return public


def assemble_messages(
    request: ChatRequest,
    history: list[dict[str, Any]],
    persona_text: str,
    memory_block: str,
    current_content: Any,
    *,
    context_summary: str = "",
) -> list[dict[str, Any]]:
    """组装发给模型的消息：人格、记忆、状态摘要、历史背景和本轮待回应内容。"""
    current_ids = set(request.source_message_ids or [request.message_id or request.dedupe_key])
    current_present = any(
        (message.get("role") == "user" or message.get("_self_message"))
        and bool(current_ids.intersection(message.get("_source_ids", [])))
        for message in history
    )
    history = protocol_history(history)
    public_history = []
    previous_date = None
    for message in history:
        if timestamp := message.get("_timestamp"):
            local = as_shanghai(datetime.fromisoformat(timestamp))
            if previous_date is not None and previous_date != local.date():
                public_history.append({"role": "user", "content": f"时间：{local:%Y-%m-%d %H:%M:%S}"})
            previous_date = local.date()
        public_history.append(_public(message, bot_name=str(request.metadata.get("bot_name") or "")))
    messages: list[dict[str, Any]] = []
    if persona_text:
        messages.append({"role": "system", "content": persona_text})
    if memory_block:
        messages.append({"role": "system", "content": memory_block})
    if context_summary:
        messages.append({"role": "system", "content": f"{SUMMARY_HEADER}\n{context_summary}"})
    messages.extend(public_history)
    if not current_present:
        messages.append(
            _public(
                {
                    "role": "user",
                    "content": current_content,
                    "_source_ids": request.source_message_ids or [request.message_id or request.dedupe_key],
                    **message_metadata(
                        timestamp=request.occurred_at,
                        sender_id=request.user_id,
                        sender_name=request.user_name,
                        quote_id=request.reply_to_message_id or "",
                        at_bot=bool(request.metadata.get("has_at_bot")),
                        sender_role=request.metadata.get("sender_role") if request.is_group else None,
                        quote_snapshot=request.metadata.get("quote_snapshot"),
                    ),
                },
                bot_name=str(request.metadata.get("bot_name") or ""),
            )
        )
    return messages


def protocol_history(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """工具调用与返回保持协议原子段，生成中的未完成调用不进入回复器。"""
    result = []
    index = 0
    while index < len(history):
        message = history[index]
        calls = message.get("tool_calls")
        if not calls:
            if message.get("role") != "tool":
                result.append(message)
            index += 1
            continue
        stop = index + 1
        while stop < len(history) and not (
            history[stop].get("role") == "assistant" and history[stop].get("_planner_internal")
        ):
            stop += 1
        following = history[index + 1 : stop]
        outputs = {item.get("tool_call_id"): item for item in following if item.get("role") == "tool"}
        ids = [call.get("id") for call in calls]
        if all(identity in outputs for identity in ids):
            result.extend([message, *(outputs[identity] for identity in ids)])
        result.extend(item for item in following if item.get("role") != "tool")
        index = stop
    return result


def truncate_content(content: Any, limit: int = MAX_CURRENT_CHARS) -> Any:
    """只截断发给模型的本轮正文，图片段保持不变。"""
    if isinstance(content, str):
        return content if len(content) <= limit else content[: limit - 1] + "…"
    if not isinstance(content, list):
        return content
    remaining = limit
    result: list[Any] = []
    for part in content:
        if not isinstance(part, dict) or part.get("type") != "text":
            result.append(part)
            continue
        text = str(part.get("text") or "")
        if len(text) <= remaining:
            result.append(part)
            remaining -= len(text)
        elif remaining > 0:
            result.append({**part, "text": text[: remaining - 1] + "…"})
            remaining = 0
    return result


def build_user_content(request: ChatRequest) -> str | list[dict[str, Any]]:
    """构造用户消息内容，带上发言人标识，引用、@ 目标和图片一并附带。"""
    text = request.text
    if request.quoted_text and not request.metadata.get("quote_snapshot"):
        text = f"{request.quoted_text}\n{text}" if text else request.quoted_text
    label = speaker_label(request.user_id, request.user_name)
    if text:
        text = f"{label}: {text}"
    else:
        # 有图片段时由图片本身承载内容，避免历史里出现两份 [图片] 占位
        text = f"{label}:" if request.images else f"{label}: [图片]"
    if request.is_group:
        if line := mentions_line(request.mentions):
            text = f"{text}\n{line}"
    if not request.images:
        return text
    content: list[dict[str, Any]] = []
    if text:
        content.append({"type": "text", "text": text})
    content.extend({"type": "image_url", "image_url": {"url": url}} for url in request.images)
    return content


def recall_query(request: ChatRequest, history: list[dict[str, Any]]) -> str:
    """检索查询：当前消息 + 最近两条历史 + @ 名字，截断 200 字。"""
    parts = [str(request.text or "")]
    parts.extend(str(message.get("content") or "") for message in history[-2:])
    parts.extend(ref.name for ref in request.mentions if ref.name)
    return " ".join(part for part in parts if part)[:200]


def strip_images(messages: list[dict[str, Any]], descriptions: list[str] | None = None) -> list[dict[str, Any]]:
    """历史只保留文本：图片换成文字描述，没有描述时用 [图片] 占位。"""
    pending = list(descriptions or [])
    cleaned: list[dict[str, Any]] = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, list):
            parts: list[str] = []
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "text":
                    parts.append(str(part.get("text") or ""))
                elif part.get("type") == "image_url":
                    description = pending.pop(0) if pending else ""
                    parts.append(f"[图片: {description}]" if description else "[图片]")
            message = {**message, "content": "\n".join(part for part in parts if part) or "[图片]"}
        cleaned.append(message)
    return cleaned


def temporary_style_note(behavior: dict[str, Any]) -> str:
    """按概率抽取本轮临时表达风格；对齐 MaiBot 的 multiple_reply_style。"""
    styles = [str(style).strip() for style in behavior.get("multiple_reply_style") or [] if str(style).strip()]
    probability = behavior.get("multiple_probability", 0)
    if not styles or not isinstance(probability, (int, float)) or probability <= 0:
        return ""
    if random.random() > probability:
        return ""
    return f"你的说话风格可以尝试：\n{random.choice(styles)}"


def extra_prompt_block(chat_cfg: dict[str, Any], request: ChatRequest) -> str:
    """匹配当前群或私聊的额外 Prompt；条目形如 平台:ID:group|private:提示词。"""
    behavior = chat_cfg.get("behavior")
    entries = behavior.get("chat_prompts") if isinstance(behavior, dict) else None
    prompts: list[str] = []
    for entry in entries or []:
        parts = [part.strip() for part in str(entry).split(":", 3)]
        if len(parts) != 4:
            continue
        platform, target_id, rule_type, prompt = parts
        if not platform or not target_id or not prompt or platform != request.platform:
            continue
        if rule_type == "group" and request.is_group and str(request.group_id or "") == target_id:
            prompts.append(prompt)
        elif rule_type == "private" and not request.is_group and request.user_id == target_id:
            prompts.append(prompt)
    return "\n".join(prompts)


def attention_drift_block(settings: Any) -> str:
    """构建注意力漂移提示块；未知档位按默认值处理。"""
    if not isinstance(settings, dict) or not settings.get("enabled"):
        return ""
    rules = (
        _DRIFT_LEVEL_RULES.get(str(settings.get("drift_level") or ""), _DRIFT_LEVEL_RULES["scattered"]),
        _ANCHOR_POLICY_RULES.get(str(settings.get("anchor_policy") or ""), _ANCHOR_POLICY_RULES["balanced"]),
        _REACTION_STYLE_RULES.get(str(settings.get("reaction_style") or ""), _REACTION_STYLE_RULES["lively"]),
    )
    return "\n".join([_ATTENTION_DRIFT_HEADER, *rules])


def build_persona_text(request: ChatRequest, chat_cfg: dict[str, Any], behavior: dict[str, Any]) -> tuple[str, str]:
    """返回本轮生效的人格名与完整人格提示。"""
    persona_name = request.metadata.get("persona_name") if isinstance(request.metadata, dict) else None
    bound_persona = get_binding(PERSONA, request.session_id)
    effective_persona = str(persona_name or bound_persona or chat_cfg["persona"] or DEFAULT_PERSONA)
    scene_prompt = behavior.get("group_chat_prompt" if request.is_group else "private_chat_prompts")
    persona_text = compose_persona_prompt(
        effective_persona,
        get_persona_text(effective_persona),
        scene_prompt=str(scene_prompt or ""),
        planner=bool(request.metadata.get("planner_loop") and not request.metadata.get("planner_reply")),
    )
    if request.metadata.get("planner_loop") and not request.metadata.get("planner_reply"):
        from ..heart_flow.prompts import planner_prompt

        persona_text = planner_prompt(
            effective_persona, get_persona_text(effective_persona), behavior, is_group=request.is_group
        )
    if extra_prompt := extra_prompt_block(chat_cfg, request):
        persona_text = f"{persona_text}\n\n当前聊天额外注意事项：\n{extra_prompt}"
    if reaction := reaction_block(chat_cfg, request.text):
        persona_text = f"{persona_text}\n\n{reaction}"
    if drift := attention_drift_block(chat_cfg.get("attention_drift")):
        persona_text = f"{persona_text}\n\n{drift}"
    aliases = [str(alias).strip() for alias in behavior.get("persona_aliases") or [] if str(alias).strip()]
    if aliases and (not request.metadata.get("planner_loop") or request.metadata.get("planner_reply")):
        persona_text = f"{persona_text}\n有人也这么叫你：{'、'.join(aliases)}。"
    if routine := work_life_prompt(chat_cfg):
        persona_text = f"{persona_text}\n\n{routine}"
    if identity := group_identity_prompt(request):
        persona_text = f"{persona_text}\n\n{identity}"
    return effective_persona, persona_text
