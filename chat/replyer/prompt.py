"""提示词组装：人格、场景、历史与本轮消息。

人格与场景的拼装方式对齐 MaiBot（commit 062d45c，`src/chat/replyer/maisaka_generator_base.py`），
按 FxBot 的单层消息结构改写。
"""

from __future__ import annotations

import random
from typing import Any

from ..types import ChatRequest, is_proactive, speaker_label
from ..bindings import PERSONA, get_binding
from ..personas import DEFAULT_PERSONA, get_persona_text, compose_persona_prompt
from ...utils.tz import now
from ..context_summary import SUMMARY_HEADER
from ..message_receive.reactions import reaction_block

MAX_BACKGROUND_CHARS = 2000
MAX_BACKGROUND_LINE_CHARS = 500
MAX_CURRENT_CHARS = 4000
CURRENT_MARK = "[需要你回应]"
BACKGROUND_HEADER = "[群聊背景，不要回复]"
_WEEKDAYS = "一二三四五六日"

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


def _flatten(content: Any) -> str:
    """把一条历史消息压成单行，合并后不会伪造出新的发言人。"""
    line = " ".join(str(content or "").split())
    return f"{line[: MAX_BACKGROUND_LINE_CHARS - 1]}…" if len(line) > MAX_BACKGROUND_LINE_CHARS else line


def _split_speaker(line: str) -> tuple[str, str]:
    """拆分「昵称(ID): 正文」，没有发言标识时返回空标签。"""
    label, sep, text = line.partition(": ")
    return (label, text) if sep else ("", line)


def background_block(messages: list[dict[str, Any]]) -> str:
    """把未触发的群消息并成一段背景；同一人连续发言合并，超预算时保留最近的。"""
    lines: list[str] = []
    dropped = len(messages)
    for index in range(len(messages) - 1, -1, -1):
        line = _flatten(messages[index].get("content"))
        if not line:
            continue
        label, text = _split_speaker(line)
        candidate = [line, *lines]
        if label and lines and _split_speaker(lines[0])[0] == label:
            merged = f"{line}；{_split_speaker(lines[0])[1]}" if text else lines[0]
            if len(merged) <= MAX_BACKGROUND_LINE_CHARS:
                candidate = [merged, *lines[1:]]
        header = BACKGROUND_HEADER if not index else f"{BACKGROUND_HEADER}（更早的 {index} 条已省略）"
        budget = MAX_BACKGROUND_CHARS - len(header) - 1
        if len("\n".join(candidate)) > budget:
            if not lines:
                lines = [line[: budget - 1] + "…"] if budget > 0 else []
                dropped = index
            break
        lines = candidate
        dropped = index
    if not lines:
        return ""
    header = BACKGROUND_HEADER if not dropped else f"{BACKGROUND_HEADER}（更早的 {dropped} 条已省略）"
    return f"{header}\n" + "\n".join(lines)


def mark_current(content: str | list[dict[str, Any]]) -> str | list[dict[str, Any]]:
    """给本轮要回答的消息加标记，与背景块区分。"""
    if isinstance(content, str):
        return f"{CURRENT_MARK}\n{content}"
    return [{"type": "text", "text": CURRENT_MARK}, *content]


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


def _public(message: dict[str, Any]) -> dict[str, Any]:
    """去掉只给运行时用的内部字段，历史条目里的来源标记不发给模型。"""
    return {key: value for key, value in message.items() if not str(key).startswith("_")}


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
    history = [_public(message) for message in history]
    messages: list[dict[str, Any]] = []
    if persona_text:
        messages.append({"role": "system", "content": persona_text})
    if memory_block:
        messages.append({"role": "system", "content": memory_block})
    if context_summary:
        messages.append({"role": "system", "content": f"{SUMMARY_HEADER}\n{context_summary}"})
    if request.is_group:
        split = len(history)
        while split and history[split - 1].get("role") == "user":
            split -= 1
        messages.extend(history[:split])
        if block := background_block(history[split:]):
            messages.append({"role": "user", "content": block})
        messages.append({"role": "user", "content": mark_current(current_content)})
    else:
        messages.extend(history)
        messages.append({"role": "user", "content": current_content})
    return messages


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


def scene_hint(request: ChatRequest, persona_name: str = "") -> str:
    """告诉模型当前是群聊还是私聊、自己的名字，以及 @ 目标的读法。"""
    if request.is_group:
        lines = [
            "\n\n你正在群聊中。只有开头带「[需要你回应]」的那条消息要你回答；"
            "「[群聊背景，不要回复]」和更早的消息只是背景，不要逐条回复、总结或重新回答。",
            "用户消息的「昵称(ID): 」是发言人标识，不是消息内容。不要把不同成员当成同一个人。",
            "「[图片: 描述]」是图片摘要。指代某人用其昵称，回复不要加「昵称: 」前缀。",
        ]
        if "[合并转发消息]" in request.text:
            lines.append("「[合并转发消息]」是一张转发卡片，需要其中的内容时用 view_forward_message 展开。")
        if persona_name:
            lines.append(f"你的名字是「{persona_name}」。")
        if request.mentions:
            lines.append(
                "消息末尾的「[@成员: 昵称(ID)]」列出了被 @ 的成员，是系统标记不是正文；"
                "用户要求对某人操作时直接用其中的 ID，没给出目标就先问清楚，不要对发起人自己执行。"
            )
        if is_proactive(request):
            lines.append(
                "本轮是主动接话：没有人在 @ 你，默认不要 @ 人、不要引用消息；"
                "顺着话题自然接一句，不需要回应或话题已经结束时保持安静。"
            )
        return "".join(lines)
    lines = ["\n\n用户正在跟你私聊。"]
    if "[合并转发消息]" in request.text:
        lines.append("「[合并转发消息]」是一张转发卡片，需要其中的内容时用 view_forward_message 展开。")
    return "".join(lines)


def time_hint() -> str:
    """注入一行当前北京时间与时段；深夜和早上附带一句语气提示。"""
    current = now()
    hour = current.hour
    advice = ""
    if hour < 6 or hour >= 23:
        period, advice = "深夜", "语气轻，不要主动问早或道晚安。"
    elif hour < 9:
        period, advice = "早上", "可以自然地问候。"
    elif hour < 11:
        period = "上午"
    elif hour < 13:
        period = "中午"
    elif hour < 18:
        period = "下午"
    else:
        period = "晚上"
    stamp = f"{current:%Y-%m-%d %H:%M} 周{_WEEKDAYS[current.weekday()]}"
    return f"\n\n现在：{stamp}，{period}。{advice}"


def build_user_content(request: ChatRequest) -> str | list[dict[str, Any]]:
    """构造用户消息内容，带上发言人标识，引用、@ 目标和图片一并附带。"""
    text = request.text
    if request.quoted_text:
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
        effective_persona, get_persona_text(effective_persona), scene_prompt=str(scene_prompt or "")
    )
    persona_text = f"{persona_text}{scene_hint(request, effective_persona)}".strip()
    if extra_prompt := extra_prompt_block(chat_cfg, request):
        persona_text = f"{persona_text}\n\n当前聊天额外注意事项：\n{extra_prompt}"
    if reaction := reaction_block(chat_cfg, request.text):
        persona_text = f"{persona_text}\n\n{reaction}"
    if drift := attention_drift_block(chat_cfg.get("attention_drift")):
        persona_text = f"{persona_text}\n\n{drift}"
    aliases = [str(alias).strip() for alias in behavior.get("persona_aliases") or [] if str(alias).strip()]
    if aliases:
        persona_text = f"{persona_text}\n有人也这么叫你：{'、'.join(aliases)}。"
    if request.is_group and behavior.get("enable_reply_at", True):
        persona_text = (
            f"{persona_text}\n\n回复时如需 @ 某人，可在开头写 @昵称；只能 @ 本条消息的发送者或消息中被 @ 的人。"
        )
    persona_text = f"{persona_text}{time_hint()}"
    return effective_persona, persona_text
