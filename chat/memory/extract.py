"""批次抽取提示、JSON 解析与校验。"""

from __future__ import annotations

import re
import json
import hashlib
from typing import Any
from dataclasses import dataclass

from nonebot import logger

from .models import ChatMessage
from .filtering import sanitize_line

FACT_CATEGORIES = frozenset({"identity", "preference", "relationship", "event", "skill", "opinion", "other"})
RELATION_OBJECT_KINDS = frozenset({"user", "org", "place", "thing"})
_SENSITIVE = re.compile(
    r"(密码|口令|token|secret|api[_ ]?key|身份证|银行卡|信用卡|手机号|\b1[3-9]\d{9}\b|住址)",
    re.I,
)
_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "topics": {
            "type": "array",
            "maxItems": 3,
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "keywords": {"type": "array"},
                    "summary": {"type": "string"},
                },
            },
        },
        "facts": {
            "type": "array",
            "maxItems": 20,
            "items": {
                "type": "object",
                "properties": {
                    "subject_id": {"type": "string"},
                    "subject_name": {"type": "string"},
                    "key": {"type": "string"},
                    "value": {"type": "string"},
                    "category": {"type": "string"},
                    "confidence": {"type": "number"},
                    "importance": {"type": "number"},
                    "asserted_by": {"type": "string"},
                    "evidence": {"type": "array"},
                },
            },
        },
        "relations": {
            "type": "array",
            "maxItems": 20,
            "items": {
                "type": "object",
                "properties": {
                    "subject_id": {"type": "string"},
                    "predicate": {"type": "string"},
                    "object_kind": {"type": "string"},
                    "object_key": {"type": "string"},
                    "object_name": {"type": "string"},
                    "object_user_id": {"type": "string"},
                    "confidence": {"type": "number"},
                    "evidence": {"type": "array"},
                },
            },
        },
        "emotion": {"type": "object"},
    },
}


@dataclass(frozen=True)
class ExtractionTopic:
    """一个抽取主题。"""

    title: str
    keywords: tuple[str, ...]
    summary: str


@dataclass(frozen=True)
class ExtractionFact:
    """一条经过校验的长期事实。"""

    subject_id: str
    subject_name: str
    key: str
    value: str
    value_hash: str
    category: str
    confidence: float
    importance: float
    asserted_by: str
    evidence: tuple[int, ...]


@dataclass(frozen=True)
class ExtractionRelation:
    """一条类型明确的关系边。"""

    subject_id: str
    predicate: str
    object_kind: str
    object_key: str
    object_name: str
    object_user_id: str
    confidence: float


@dataclass(frozen=True)
class ExtractionResult:
    """一次抽取的完整结果。"""

    topics: tuple[ExtractionTopic, ...]
    facts: tuple[ExtractionFact, ...]
    emotion: tuple[float, float] | None = None
    relations: tuple[ExtractionRelation, ...] = ()


def normalize_key(category: str, key: str) -> str:
    """谓词统一小写去标点并带 category 前缀。"""
    value = re.sub(r"[\s\W_]+", "_", str(key or "").casefold()).strip("_")
    prefix = f"{category}."
    if not value:
        return ""
    return value if value.startswith(prefix) else f"{prefix}{value}"


def _normalized_value(value: str) -> str:
    """值归一化后用于去重指纹。"""
    return " ".join(sanitize_line(value, 200).casefold().split())


def _normalize_predicate(value: object) -> str:
    """关系谓词压成短标识，中英文都保留。"""
    return re.sub(r"[\s\W_]+", "_", str(value or "").casefold()).strip("_")[:40]


def _clamp(value: object, default: float, low: float = 0.0, high: float = 1.0) -> float:
    """把模型给出的置信/重要性夹到合理区间。"""
    try:
        return max(low, min(high, float(value)))
    except (TypeError, ValueError):
        return default


def build_prompt(messages: list[ChatMessage]) -> list[dict[str, Any]]:
    """构造抽取消息；只包含本批次可信消息。"""
    lines = []
    for message in messages:
        name = message.sender_name or "用户"
        lines.append(f"[{int(message.id or 0)}] {name}({message.sender_id}): {message.content}")
    system = (
        "你是群聊长期记忆抽取器。只输出 JSON，不要解释。\n"
        "只从给定的人类消息中抽取稳定事实、最多 3 个主题和类型明确的关系；"
        "不要抽取密码、令牌、身份证、银行卡、手机号、住址。\n"
        "fact 的 subject_id 和 asserted_by 必须是输入中出现过的用户 ID；evidence 必须使用方括号里的消息序号。\n"
        "key 使用简短谓词，例如 identity.name；value 保持原文含义，不编造。\n"
        "relation 的 subject_id 必须是输入中出现过的用户 ID；object_kind 只能是 user/org/place/thing。\n"
        "object_kind=user 时 object_key 和 object_user_id 必须是输入中出现过的用户 ID；"
        "公司、地点、物品用 object_name 表示，object_user_id 留空；关系必须有 evidence。\n"
        '输出格式：{"topics":[{"title":"","keywords":[],"summary":""}],'
        '"facts":[{"subject_id":"","subject_name":"","key":"","value":"","category":"identity",'
        '"confidence":0.8,"importance":0.6,"asserted_by":"","evidence":[1]}],'
        '"relations":[{"subject_id":"","predicate":"","object_kind":"user","object_key":"",'
        '"object_name":"","object_user_id":"","confidence":0.7,"evidence":[1]}],'
        '"emotion":{"valence":0.0,"arousal":0.0}}'
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": "\n".join(lines)}]


def parse_extraction(data: object, messages: list[ChatMessage]) -> ExtractionResult | None:
    """校验模型输出，丢弃越权主体、越界证据和敏感内容。"""
    if not isinstance(data, dict):
        return None
    authors = {str(message.sender_id): message.sender_name for message in messages if message.sender_id}
    allowed_ids = {int(message.id) for message in messages if message.id is not None}
    topics: list[ExtractionTopic] = []
    raw_topics = data.get("topics")
    for item in raw_topics[:3] if isinstance(raw_topics, list) else []:
        if not isinstance(item, dict):
            continue
        title = sanitize_line(item.get("title"), 60)
        summary = sanitize_line(item.get("summary"), 240)
        if not title and not summary:
            continue
        keywords = item.get("keywords")
        topics.append(
            ExtractionTopic(
                title=title,
                keywords=tuple(sanitize_line(word, 20) for word in keywords[:8] if str(word).strip())
                if isinstance(keywords, list)
                else (),
                summary=summary,
            )
        )
    facts: list[ExtractionFact] = []
    raw_facts = data.get("facts")
    for item in raw_facts[:20] if isinstance(raw_facts, list) else []:
        if not isinstance(item, dict):
            continue
        subject_id = str(item.get("subject_id") or "").strip()
        asserted_by = str(item.get("asserted_by") or subject_id).strip()
        if subject_id not in authors or asserted_by not in authors:
            continue
        value = sanitize_line(item.get("value"), 120)
        if not value or _SENSITIVE.search(value):
            continue
        category = str(item.get("category") or "other").strip().lower()
        if category not in FACT_CATEGORIES:
            category = "other"
        key = normalize_key(category, str(item.get("key") or ""))
        if not key:
            continue
        raw_evidence = item.get("evidence")
        evidence = ()
        if isinstance(raw_evidence, list):
            evidence = tuple(
                sorted({int(seq) for seq in raw_evidence if str(seq).strip().isdigit() and int(seq) in allowed_ids})
            )
        if not evidence:
            continue
        facts.append(
            ExtractionFact(
                subject_id=subject_id,
                subject_name=sanitize_line(item.get("subject_name") or authors.get(subject_id, ""), 32),
                key=key,
                value=value,
                value_hash=hashlib.sha1(_normalized_value(value).encode("utf-8")).hexdigest(),
                category=category,
                confidence=_clamp(item.get("confidence"), 0.6),
                importance=_clamp(item.get("importance"), 0.5),
                asserted_by=asserted_by,
                evidence=evidence,
            )
        )
    relations: list[ExtractionRelation] = []
    seen_relations: set[tuple[str, str, str, str]] = set()
    batch_text = " ".join(str(message.content or "") for message in messages).casefold()
    raw_relations = data.get("relations")
    for item in raw_relations[:20] if isinstance(raw_relations, list) else []:
        if not isinstance(item, dict):
            continue
        subject_id = str(item.get("subject_id") or "").strip()
        if subject_id not in authors:
            continue
        predicate = _normalize_predicate(item.get("predicate"))
        if not predicate:
            continue
        object_kind = str(item.get("object_kind") or "").strip().lower()
        if object_kind not in RELATION_OBJECT_KINDS:
            continue
        raw_evidence = item.get("evidence")
        evidence = ()
        if isinstance(raw_evidence, list):
            evidence = tuple(
                sorted({int(seq) for seq in raw_evidence if str(seq).strip().isdigit() and int(seq) in allowed_ids})
            )
        if not evidence:
            continue
        if object_kind == "user":
            # 用户边必须落到本批次出现过的人，公司名之类不能冒充用户
            candidate = str(item.get("object_key") or "").strip()
            if candidate not in authors:
                candidate = str(item.get("object_user_id") or "").strip()
            if candidate not in authors:
                continue
            object_key = candidate
            object_name = sanitize_line(authors[candidate], 32)
            object_user_id = candidate
        else:
            object_name = sanitize_line(item.get("object_name"), 60)
            if not object_name or _SENSITIVE.search(object_name):
                continue
            object_key = _normalized_value(object_name)[:80]
            if not object_key or object_key not in batch_text:
                continue
            object_user_id = ""
        fingerprint = (subject_id, predicate, object_kind, object_key)
        if fingerprint in seen_relations:
            continue
        seen_relations.add(fingerprint)
        relations.append(
            ExtractionRelation(
                subject_id=subject_id,
                predicate=predicate,
                object_kind=object_kind,
                object_key=object_key,
                object_name=object_name,
                object_user_id=object_user_id,
                confidence=_clamp(item.get("confidence"), 0.6),
            )
        )
    emotion: tuple[float, float] | None = None
    raw_emotion = data.get("emotion")
    if isinstance(raw_emotion, dict):
        valence = raw_emotion.get("valence")
        arousal = raw_emotion.get("arousal")
        if isinstance(valence, (int, float)) and isinstance(arousal, (int, float)):
            emotion = (_clamp(valence, 0.0, -1.0, 1.0), _clamp(arousal, 0.0, -1.0, 1.0))
    return ExtractionResult(topics=tuple(topics), facts=tuple(facts), emotion=emotion, relations=tuple(relations))


async def request_extraction(provider: Any, messages: list[ChatMessage], *, model: str = "") -> ExtractionResult | None:
    """调用抽取 Provider，失败重试一次；仍失败返回 None。"""
    prompt = build_prompt(messages)
    for attempt in range(2):
        try:
            data = await provider.chat_json(prompt, _SCHEMA, model=model or None)
        except Exception as exc:
            logger.opt(exception=True).debug(f"[ChatMemory] 抽取调用失败（第 {attempt + 1} 次）: {type(exc).__name__}")
            continue
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError:
                data = None
        result = parse_extraction(data, messages)
        if result is not None:
            return result
    return None
