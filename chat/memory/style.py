"""群风格与黑话：批次学习、JSON 校验、定时任务。"""

from __future__ import annotations

import json
import asyncio
from typing import Any
from datetime import datetime, timedelta
from dataclasses import replace, dataclass

from nonebot import logger

from .state import is_ready
from .config import jargon_config, expression_config, tenant_learn_enabled
from .models import ChatMessage, utc_now_naive
from .learning import tenant_target, learning_flags
from .filtering import SENSITIVE, should_learn, sanitize_line
from .repository import TenantJobState, chat_repository
from ..providers.routing import fallback_provider

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
    """调用风格模型，失败重试一次；合法空结果原样返回，由调用方推进扫描。"""
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
        if result is not None:
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


async def _call_style_model(
    tenant: str, messages: list[ChatMessage], known_terms: list[str], provider: Any
) -> StyleResult | None:
    """在事务外调用风格模型。"""
    if provider is not None:
        return await request_style(provider, messages, known_terms)
    try:
        async with fallback_provider("style", tenant) as leased:
            return await request_style(leased, messages, known_terms)
    except Exception as exc:
        logger.opt(exception=True).warning(f"[ChatStyle] 风格 Provider 不可用: {type(exc).__name__}")
        return None


async def learn_style(tenant: str, *, provider: Any = None, now: datetime | None = None) -> bool:
    """每周或新增 200 条人类消息后学习一次；成功提交返回 True。"""
    if not is_ready():
        return False
    state = await chat_repository.get_session_state(tenant)
    if not tenant_learn_enabled(state):
        return False
    _, expression_learn = learning_flags(expression_config().get("learning_list"), tenant_target(tenant))
    if not expression_learn:
        return False
    style = await chat_repository.get_style(tenant)
    # 扫描游标记录已确认无法贡献的消息，避免无效前缀把学习永久卡住
    scan = await chat_repository.get_cursor(tenant, "style")
    after = max(int(style.evidence_seq_end) if style else 0, int(scan.cursor_seq) if scan else 0)
    raw = await chat_repository.messages_after(tenant, after, limit=max(_MAX_MESSAGES * 2, 400))
    if not raw:
        return False
    clean = _clean_messages(raw, tenant)
    if not clean:
        await chat_repository.set_cursor(tenant, "style", int(raw[-1].id or 0))
        return False
    moment = now or utc_now_naive()
    # 证据被撤回后会标记重建：不再等积满 200 条，直接用剩余证据重学
    rebuild = bool(style is not None and style.needs_rebuild)
    enough = len(clean) >= _MAX_MESSAGES or rebuild
    if not enough and (style is None or moment - style.updated_at < timedelta(days=_INTERVAL_DAYS)):
        # 只越过本批的无效前缀，已清洗出的消息留给后续批次继续累计
        await chat_repository.set_cursor(tenant, "style", int(clean[0].id or 0) - 1)
        return False
    selected = clean[-_MAX_MESSAGES:]
    known_terms = [row.term for row in await chat_repository.list_jargon(tenant, status="active", limit=200)]
    result = await _call_style_model(tenant, selected, known_terms, provider)
    if result is None:
        return False
    if not (result.rules or result.summary or result.jargon):
        await chat_repository.set_cursor(tenant, "style", int(raw[-1].id or 0))
        return False
    _, jargon_learn = learning_flags(jargon_config().get("learning_list"), tenant_target(tenant))
    if not jargon_learn:
        result = replace(result, jargon=())
    seq_start = int(selected[0].id or 0)
    seq_end = int(selected[-1].id or 0)
    saved = await chat_repository.save_style(
        tenant,
        result,
        seq_start=seq_start,
        seq_end=seq_end,
        evidence_count=len(selected),
        source_batch_id=style_batch_id(seq_start, seq_end),
        expected_manual_version=int(style.manual_version) if style else 0,
        evidence_ids=tuple(int(message.id or 0) for message in selected),
        expected_exists=style is not None,
    )
    if saved:
        await chat_repository.set_cursor(tenant, "style", int(raw[-1].id or 0))
    return saved


def _tick_guard() -> asyncio.Lock:
    global _style_lock
    if _style_lock is None:
        _style_lock = asyncio.Lock()
    return _style_lock


def _learn_enabled(candidate: TenantJobState) -> bool:
    """本租户显式关闭时跳过；未设置按开启处理。"""
    return True if candidate.learn_enabled is None else bool(candidate.learn_enabled)


async def style_job_tick(*, provider: Any = None, now: datetime | None = None) -> int:
    """处理所有租户；并发上限只限制同时运行的任务，候选不再被截断。"""
    if not is_ready():
        return 0
    config = expression_config()
    moment = now or utc_now_naive()
    async with _tick_guard():
        try:
            snapshot = await chat_repository.scheduling_snapshot()
        except Exception:
            logger.opt(exception=True).debug("[ChatStyle] 读取风格学习租户失败")
            return 0
        candidates = sorted(
            (candidate for candidate in snapshot if _learn_enabled(candidate)),
            key=lambda item: item.last_message_id,
            reverse=True,
        )
        semaphore = asyncio.Semaphore(max(1, int(config["max_expression_learner"])))

        async def run(candidate: TenantJobState) -> bool:
            async with semaphore:
                return await learn_style(candidate.tenant, provider=provider, now=moment)

        results = await asyncio.gather(*(run(candidate) for candidate in candidates), return_exceptions=True)
        for candidate, result in zip(candidates, results):
            if isinstance(result, BaseException):
                logger.opt(exception=result).warning(f"[ChatStyle] 租户风格学习失败: {candidate.tenant}")
        return sum(1 for result in results if result is True)
