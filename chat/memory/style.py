"""群风格与黑话：批次学习、JSON 校验、定时任务。"""

from __future__ import annotations

import json
import asyncio
from typing import Any
from datetime import datetime, timedelta
from dataclasses import dataclass

from nonebot import logger

from .state import is_ready
from .config import memory_config, style_model_name, tenant_learn_enabled, style_learning_enabled
from .models import ChatMessage, utc_now_naive
from .extract import SENSITIVE
from ...config import get_manager as get_config_manager
from ..bindings import PROVIDER, get_binding
from .filtering import should_learn, sanitize_line
from ..providers import provider_manager
from .repository import TenantJobState, chat_repository

_MAX_MESSAGES = 200
_INTERVAL_DAYS = 7
_MAX_RULES = 12
_MAX_EXAMPLES = 10
_MAX_JARGON = 20
_MIN_JARGON_CONFIDENCE = 0.6

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "rules": {"type": "array"},
        "examples": {"type": "array"},
        "summary": {"type": "string"},
        "jargon": {"type": "array"},
    },
}
_style_lock: asyncio.Lock | None = None


@dataclass(frozen=True)
class StyleExample:
    """一条逐字摘自原始消息的风格示例。"""

    text: str
    seq: int


@dataclass(frozen=True)
class StyleJargon:
    """一个群内特有词；低置信释义标记为 rejected。"""

    term: str
    normalized: str
    meaning: str
    status: str


@dataclass(frozen=True)
class StyleResult:
    """一次风格学习的完整结果。"""

    rules: tuple[str, ...]
    examples: tuple[StyleExample, ...]
    summary: str
    jargon: tuple[StyleJargon, ...]


def style_batch_id(seq_start: int, seq_end: int) -> str:
    """黑话来源批次只由证据消息范围决定，撤回时据此失效。"""
    return f"style:{int(seq_start)}:{int(seq_end)}"


def _clamp_confidence(value: object) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def build_prompt(messages: list[ChatMessage], known_terms: list[str] | tuple[str, ...] = ()) -> list[dict[str, Any]]:
    """构造风格分析消息；只包含本批次可信消息。"""
    lines = []
    for message in messages:
        name = message.sender_name or "用户"
        lines.append(f"[{int(message.id or 0)}] {name}({message.sender_id}): {message.content}")
    system = (
        "你是群聊风格分析器。只输出 JSON，不要解释。\n"
        "从给定的人类消息中总结这个群的说话风格：常用称呼、语气、句长、标点和表情习惯、互动方式。\n"
        "rules 是可直接指导回复的短句，最多 12 条，不复述个人隐私。\n"
        "examples 必须逐字摘自某条消息，seq 使用方括号里的消息序号，最多 10 条；"
        "不要包含联系方式、链接、密码、令牌、身份证、银行卡、手机号、住址。\n"
        "summary 用不超过 150 字概括整体风格。\n"
        "jargon 只抽取下面已知列表之外的群内特有词；term 必须出现在消息中，"
        "meaning 是简短释义，confidence 低于 0.6 的词会被丢弃。\n"
        '输出格式：{"rules":[""],"examples":[{"text":"","seq":1}],"summary":"",'
        '"jargon":[{"term":"","meaning":"","confidence":0.8}]}'
    )
    known = [str(term) for term in known_terms if str(term).strip()][:50]
    if known:
        system += "\n已知黑话（不要重复抽取）：" + "、".join(known)
    return [{"role": "system", "content": system}, {"role": "user", "content": "\n".join(lines)}]


def parse_style(
    data: object, messages: list[ChatMessage], known_terms: list[str] | tuple[str, ...] = ()
) -> StyleResult | None:
    """校验模型输出：示例必须逐字出自引用消息，词条必须出现在批次里。"""
    if not isinstance(data, dict):
        return None
    by_id = {int(message.id): message for message in messages if message.id is not None}
    batch_text = " ".join(str(message.content or "") for message in messages).casefold()
    known = {" ".join(str(term).casefold().split()) for term in known_terms}

    rules: list[str] = []
    raw_rules = data.get("rules")
    for item in raw_rules[:_MAX_RULES] if isinstance(raw_rules, list) else []:
        text = sanitize_line(item, 60)
        if not text or SENSITIVE.search(text) or text in rules:
            continue
        rules.append(text)

    examples: list[StyleExample] = []
    raw_examples = data.get("examples")
    for item in raw_examples[:_MAX_EXAMPLES] if isinstance(raw_examples, list) else []:
        if not isinstance(item, dict):
            continue
        seq_text = str(item.get("seq") or "").strip()
        if not seq_text.isdigit() or int(seq_text) not in by_id:
            continue
        text = " ".join(str(item.get("text") or "").split())
        if not text or SENSITIVE.search(text):
            continue
        if text not in " ".join(str(by_id[int(seq_text)].content or "").split()):
            continue
        text = sanitize_line(text, 80)
        example = StyleExample(text=text, seq=int(seq_text))
        if example not in examples:
            examples.append(example)

    summary = sanitize_line(data.get("summary"), 150)
    if SENSITIVE.search(summary):
        summary = ""

    jargon: list[StyleJargon] = []
    seen_terms: set[str] = set()
    raw_jargon = data.get("jargon")
    for item in raw_jargon[:_MAX_JARGON] if isinstance(raw_jargon, list) else []:
        if not isinstance(item, dict):
            continue
        term = sanitize_line(item.get("term"), 20)
        meaning = sanitize_line(item.get("meaning"), 60)
        if not term or not meaning or SENSITIVE.search(meaning):
            continue
        normalized = " ".join(term.casefold().split())
        if not normalized or normalized in known or normalized in seen_terms or normalized not in batch_text:
            continue
        seen_terms.add(normalized)
        status = "active" if _clamp_confidence(item.get("confidence")) >= _MIN_JARGON_CONFIDENCE else "rejected"
        jargon.append(StyleJargon(term=term, normalized=normalized, meaning=meaning, status=status))

    return StyleResult(
        rules=tuple(rules),
        examples=tuple(examples),
        summary=summary,
        jargon=tuple(jargon),
    )


async def request_style(
    provider: Any,
    messages: list[ChatMessage],
    known_terms: list[str] | tuple[str, ...] = (),
    *,
    model: str = "",
) -> StyleResult | None:
    """调用风格模型，失败重试一次；空结果按失败处理。"""
    prompt = build_prompt(messages, known_terms)
    for attempt in range(2):
        try:
            data = await provider.chat_json(prompt, _SCHEMA, model=model or None)
        except Exception as exc:
            logger.opt(exception=True).debug(f"[ChatStyle] 风格调用失败（第 {attempt + 1} 次）: {type(exc).__name__}")
            continue
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError:
                data = None
        result = parse_style(data, messages, known_terms)
        if result is not None and (result.rules or result.summary or result.jargon):
            return result
    return None


def _clean_messages(messages: list[ChatMessage], tenant: str) -> list[ChatMessage]:
    """过滤命令、广告与无意义内容；风格学习保留重复口头禅。"""
    result: list[ChatMessage] = []
    for message in messages:
        content = " ".join(str(message.content or "").split())
        if should_learn(content, tenant=tenant, check_duplicate=False):
            result.append(message)
    return result


def _recent_budget(messages: list[ChatMessage], limit: int) -> list[ChatMessage]:
    """从最新一条往前取，整体字符数不超过预算。"""
    kept: list[ChatMessage] = []
    used = 0
    for message in reversed(messages):
        size = len(message.content or "")
        if kept and used + size > limit:
            break
        kept.append(message)
        used += size
    return list(reversed(kept))


async def _call_style_model(
    tenant: str, messages: list[ChatMessage], known_terms: list[str], provider: Any
) -> StyleResult | None:
    """在事务外调用风格模型。"""
    model = style_model_name()
    if provider is not None:
        return await request_style(provider, messages, known_terms, model=model)
    try:
        provider_name = get_binding(PROVIDER, tenant)
        if not provider_name:
            provider_name = str(get_config_manager().get_system()["chat"].get("provider") or "")
        async with provider_manager.lease(provider_name) as leased:
            return await request_style(leased, messages, known_terms, model=model)
    except Exception as exc:
        logger.opt(exception=True).warning(f"[ChatStyle] 风格 Provider 不可用: {type(exc).__name__}")
        return None


async def learn_style(
    tenant: str, *, provider: Any = None, now: datetime | None = None, cfg: dict[str, Any] | None = None
) -> bool:
    """每周或新增 200 条人类消息后学习一次；成功提交返回 True。"""
    if not is_ready() or not style_learning_enabled():
        return False
    config = cfg or memory_config()
    state = await chat_repository.get_session_state(tenant)
    if not tenant_learn_enabled(state):
        return False
    style = await chat_repository.get_style(tenant)
    after = int(style.evidence_seq_end) if style else 0
    raw = await chat_repository.messages_after(tenant, after, limit=max(_MAX_MESSAGES * 2, 400))
    clean = _clean_messages(raw, tenant)
    if not clean:
        return False
    moment = now or utc_now_naive()
    if style is None:
        if len(clean) < _MAX_MESSAGES:
            return False
    elif len(clean) < _MAX_MESSAGES and moment - style.updated_at < timedelta(days=_INTERVAL_DAYS):
        return False
    selected = _recent_budget(clean[-_MAX_MESSAGES:], int(config["max_input_chars_per_batch"]))
    if not selected:
        return False
    known_terms = [row.term for row in await chat_repository.list_jargon(tenant, status="active", limit=200)]
    result = await _call_style_model(tenant, selected, known_terms, provider)
    if result is None:
        return False
    seq_start = int(selected[0].id or 0)
    seq_end = int(selected[-1].id or 0)
    return await chat_repository.save_style(
        tenant,
        result,
        seq_start=seq_start,
        seq_end=seq_end,
        evidence_count=len(selected),
        source_batch_id=style_batch_id(seq_start, seq_end),
        expected_manual_version=int(style.manual_version) if style else 0,
    )


def _tick_guard() -> asyncio.Lock:
    global _style_lock
    if _style_lock is None:
        _style_lock = asyncio.Lock()
    return _style_lock


def _learn_enabled(candidate: TenantJobState, cfg: dict[str, Any]) -> bool:
    if candidate.learn_enabled is not None:
        return bool(candidate.learn_enabled)
    return bool(cfg["learn_enabled"])


async def style_job_tick(
    *, provider: Any = None, now: datetime | None = None, cfg: dict[str, Any] | None = None
) -> int:
    """每小时检查一次到期租户；每轮最多提交 max_tenants_per_tick 个。"""
    if not is_ready() or not style_learning_enabled():
        return 0
    config = cfg or memory_config()
    moment = now or utc_now_naive()
    async with _tick_guard():
        try:
            snapshot = await chat_repository.scheduling_snapshot()
        except Exception:
            logger.opt(exception=True).debug("[ChatStyle] 读取风格学习租户失败")
            return 0
        processed = 0
        for candidate in sorted(snapshot, key=lambda item: item.last_message_id, reverse=True):
            if not _learn_enabled(candidate, config):
                continue
            try:
                if await learn_style(candidate.tenant, provider=provider, now=moment, cfg=config):
                    processed += 1
            except Exception:
                logger.opt(exception=True).warning(f"[ChatStyle] 租户风格学习失败: {candidate.tenant}")
            if processed >= int(config["max_tenants_per_tick"]):
                break
        return processed
