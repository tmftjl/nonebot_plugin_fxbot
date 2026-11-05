"""表达学习：情境到说法的抽取、合并、复审与选择。"""

from __future__ import annotations

import re
import json
import random
from typing import Any
from contextlib import asynccontextmanager
from dataclasses import dataclass

from nonebot import logger
from json_repair import repair_json

from .state import is_ready
from .config import expression_config, tenant_learn_enabled, expression_vector_config
from .models import ChatMessage, ChatExpression
from .prompts import learning_prompt
from .learning import tenant_target, learning_flags
from .filtering import SENSITIVE, text_hash, should_learn, sanitize_line, normalize_expression_style
from .repository import chat_repository
from .expression_vector import select_by_vectors
from ..providers.routing import routed_task, fallback_provider

_BATCH_SIZE = 40
_MIN_MESSAGES = 10
_MAX_ITEMS = 20
_COUNT_STEP = 1.0
_EXPRESSION_TYPES = frozenset({"phrase", "tone", "structure", "emoji"})
MAX_SOURCE_LENGTH = 200_000
CHUNK_LENGTH = 4_500
_MAX_TEXT_ITEMS = 30


@dataclass(frozen=True)
class ExpressionItem:
    """一条从批次中抽出的情境与说法。"""

    situation: str
    style: str
    type: str = "phrase"
    source_seq: int = 0


@dataclass(frozen=True)
class TextExpressionCandidate:
    """从粘贴文本抽出的候选；evidence 只用于控制台预览。"""

    situation: str
    style: str
    evidence: str = ""


def normalize(text: object) -> str:
    """匹配与去重都使用同一份小写、空白归一化文本。"""
    return " ".join(str(text or "").casefold().split())


def expression_batch_id(seq_start: int, seq_end: int) -> str:
    """来源批次只由消息范围决定，撤回时据此失效。"""
    return f"expression:{int(seq_start)}:{int(seq_end)}"


def build_learn_prompt(messages: list[ChatMessage]) -> list[dict[str, Any]]:
    """构造表达抽取消息；只包含本批次可信的人类消息。"""
    lines = [
        f"[id:{int(message.id or 0)}] USER {message.sender_name or '用户'}: {message.content}" for message in messages
    ]
    return [{"role": "user", "content": learning_prompt("learn_style", chat_str="\n".join(lines))}]


def parse_expressions(data: object, messages: list[ChatMessage]) -> list[ExpressionItem] | None:
    """校验模型输出；无法解析返回 None，合法空结果返回空列表。"""
    if not isinstance(data, list):
        return None
    raw_items = data
    if len(raw_items) > _MAX_ITEMS:
        return []
    by_id = {int(message.id): message for message in messages if message.id is not None}
    items: list[ExpressionItem] = []
    seen: set[tuple[str, str]] = set()
    for raw in raw_items[:_MAX_ITEMS]:
        if not isinstance(raw, dict):
            continue
        situation = sanitize_line(raw.get("situation"), 60)
        style = normalize_expression_style(sanitize_line(raw.get("style"), 60))
        if not situation or not style or SENSITIVE.search(situation) or SENSITIVE.search(style):
            continue
        key = (normalize(situation), normalize(style))
        if key in seen:
            continue
        seen.add(key)
        kind = str(raw.get("type") or "phrase").strip().lower()
        seq_text = str(raw.get("id") or "").strip()
        source_seq = int(seq_text) if seq_text.isdigit() and int(seq_text) in by_id else 0
        if not source_seq:
            continue
        if any(marker in situation or marker in style for marker in ("SELF", "[图片", "[表情包")):
            continue
        items.append(
            ExpressionItem(
                situation=situation,
                style=style,
                type=kind if kind in _EXPRESSION_TYPES else "phrase",
                source_seq=source_seq,
            )
        )
    return items


async def request_expressions(
    provider: Any, messages: list[ChatMessage], *, model: str = ""
) -> list[ExpressionItem] | None:
    """调用表达模型，失败重试一次；合法空结果不按失败处理。"""
    prompt = build_learn_prompt(messages)
    for attempt in range(2):
        try:
            response = await provider.chat(messages=prompt, model=model or None, max_tokens=8192)
            data = json.loads(repair_json(response.content))
        except Exception as exc:
            logger.opt(exception=True).debug(
                f"[ChatExpression] 表达调用失败（第 {attempt + 1} 次）: {type(exc).__name__}"
            )
            continue
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError:
                data = None
        parsed = parse_expressions(data, messages)
        if parsed is not None:
            return parsed
    return None


async def request_review(provider: Any, items: list[dict[str, Any]], *, model: str = "") -> list[bool]:
    """逐条执行写入前复审，失败仅拒绝当前条目，不影响其余候选。"""
    if not items:
        return []
    criteria = "\n".join(
        [
            "1. 表达方式或言语风格是否与使用条件或使用情景匹配",
            "2. 允许部分语法错误或口语化或缺省出现",
            "3. 表达方式不能太过特指，需要具有泛用性",
            "4. 一般不涉及具体的人名或名称",
        ]
    )
    decisions = []
    for item in items:
        try:
            prompt = learning_prompt(
                "expression_evaluation", situation=item["situation"], style=item["style"], criteria_list=criteria
            )
            response = await provider.chat(
                messages=[{"role": "user", "content": prompt}], model=model or None, max_tokens=8192
            )
            data = json.loads(repair_json(response.content))
        except Exception as exc:
            logger.opt(exception=True).debug(f"[ChatExpression] 表达复审失败: {type(exc).__name__}")
            decisions.append(False)
            continue
        if not isinstance(data, dict) or not isinstance(data.get("suitable"), bool):
            decisions.append(False)
        else:
            decisions.append(data["suitable"])
    return decisions


def merge_candidates(
    items: list[ExpressionItem], rows: list[ChatExpression]
) -> tuple[list[dict[str, Any]], dict[int, tuple[float, int, float]]]:
    """把新条目并入相似表达；返回待插入条目与已有条目的计数更新（含本批增量）。"""
    pool: list[dict[str, Any]] = [
        {
            "id": int(row.id or 0),
            "situation": row.situation,
            "style": row.style,
            "count": float(row.count),
            "base": float(row.count),
            "learn": 0,
            "merged": False,
            "new": False,
        }
        for row in rows
    ]
    new_items: list[dict[str, Any]] = []
    for item in items:
        target = _best_match(item, pool)
        if target is None:
            target = {
                "situation": item.situation,
                "style": item.style,
                "type": item.type,
                "count": 0.0,
                "base": 0.0,
                "learn": 0,
                "source_message_seq": item.source_seq,
                "merged": False,
                "new": True,
            }
            pool.append(target)
            new_items.append(target)
        target["count"] = float(target["count"]) + _COUNT_STEP
        target["learn"] = int(target["learn"]) + 1
        target["merged"] = True
    updates = {
        int(row["id"]): (float(row["count"]), int(row["learn"]), float(row["count"]) - float(row["base"]))
        for row in pool
        if not row["new"] and row["merged"]
    }
    return new_items, updates


def _best_match(item: ExpressionItem, pool: list[dict[str, Any]]) -> dict[str, Any] | None:
    """只合并情境与表达均完全一致的记录。"""
    situation_hash = text_hash(item.situation)
    style_hash = text_hash(item.style)
    for row in pool:
        if text_hash(row["situation"]) == situation_hash and text_hash(row["style"]) == style_hash:
            return row
    return None


def _clean_messages(messages: list[ChatMessage], tenant: str) -> list[ChatMessage]:
    """过滤命令、广告与无意义内容；保留重复口头禅。"""
    result: list[ChatMessage] = []
    for message in messages:
        content = " ".join(str(message.content or "").split())
        if should_learn(content, tenant=tenant, check_duplicate=False):
            result.append(message)
    return result


@asynccontextmanager
async def _lease(tenant: str, provider: Any):
    """复用调用方 Provider，否则按表达学习任务路由租用并允许回退。"""
    if provider is not None:
        yield provider
        return
    async with fallback_provider(routed_task("learner", "utils"), tenant) as leased:
        yield leased


def split_learning_text(text: str) -> list[str]:
    """按段落、换行和句末切成不超过 CHUNK_LENGTH 的文段。"""
    if not text.strip():
        raise ValueError("请先输入要学习的文段")
    if len(text) > MAX_SOURCE_LENGTH:
        raise ValueError(f"文段不能超过 {MAX_SOURCE_LENGTH:,} 字")
    chunks: list[str] = []
    while len(text) > CHUNK_LENGTH:
        window = text[:CHUNK_LENGTH]
        cut = CHUNK_LENGTH
        for boundary in (r"\n\s*\n", r"\n", r"[。！？]|[.!?](?:[\s”’\"']|$)"):
            matches = [match.end() for match in re.finditer(boundary, window) if match.end() >= CHUNK_LENGTH // 2]
            if matches:
                cut = matches[-1]
                break
        chunks.append(text[:cut])
        text = text[cut:]
    if text:
        chunks.append(text)
    return chunks


def build_learning_sources(text: str) -> dict[int, str]:
    """给原文片段编号，回到原文取依据，避免模型改写引文。"""
    sources: dict[int, str] = {}
    for line in text.splitlines():
        remaining = line.strip()
        while remaining:
            cut = min(400, len(remaining))
            if len(remaining) > 400:
                boundaries = [
                    match.end()
                    for match in re.finditer(r"[。！？；;.!?]\s*|\s+", remaining[:400])
                    if match.end() >= 200
                ]
                if boundaries:
                    cut = boundaries[-1]
            excerpt = remaining[:cut].strip()
            if excerpt:
                sources[len(sources) + 1] = excerpt
            remaining = remaining[cut:].lstrip()
    return sources


def parse_text_expressions(response: str, sources: dict[int, str]) -> list[TextExpressionCandidate]:
    """校验粘贴文本的提取结果，保留可选的原文依据。"""
    payload = response.strip()
    if not payload:
        raise ValueError("提取模型没有返回内容")
    try:
        data = json.loads(repair_json(payload))
    except Exception as exc:
        raise ValueError("提取模型返回的 JSON 无法解析") from exc
    raw_items = data.get("candidates") if isinstance(data, dict) else None
    if not isinstance(raw_items, list):
        raise ValueError("提取模型返回的候选格式不正确")
    if len(raw_items) > _MAX_TEXT_ITEMS:
        raise ValueError("单段返回的候选过多")
    items: list[TextExpressionCandidate] = []
    seen: set[tuple[str, str]] = set()
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        situation = sanitize_line(raw.get("situation") or raw.get("title"), 60)
        style = normalize_expression_style(sanitize_line(raw.get("style") or raw.get("description"), 60))
        if not situation or not style or SENSITIVE.search(situation) or SENSITIVE.search(style):
            continue
        if any(marker in situation or marker in style for marker in ("SELF", "[图片", "[表情包")):
            continue
        key = (normalize(situation), normalize(style))
        if key in seen:
            continue
        seen.add(key)
        source_id = raw.get("source_id")
        evidence = sources.get(source_id, "") if isinstance(source_id, int) else ""
        items.append(TextExpressionCandidate(situation=situation, style=style, evidence=evidence[:500]))
    return items


async def extract_text_expressions(text: str, tenant: str, *, provider: Any = None) -> list[TextExpressionCandidate]:
    """调用 learner 模型从一段粘贴文本中抽取表达候选。"""
    sources = build_learning_sources(text)
    payload = json.dumps(
        [{"id": source_id, "text": excerpt} for source_id, excerpt in sources.items()],
        ensure_ascii=False,
    )
    messages = [
        {"role": "system", "content": learning_prompt("text_learning_expression")},
        {"role": "user", "content": payload},
    ]
    async with _lease(tenant, provider) as model:
        response = await model.chat(messages=messages, max_tokens=8192)
    return parse_text_expressions(response.content, sources)


async def learn_expressions(
    tenant: str,
    *,
    provider: Any = None,
    cfg: dict[str, Any] | None = None,
    messages: list[ChatMessage] | None = None,
) -> bool:
    """累计至少 10 条消息后学习一批；成功提交返回 True。"""
    if not is_ready():
        return False
    config = cfg or expression_config()
    _, learn = learning_flags(config.get("learning_list"), tenant_target(tenant))
    if not learn:
        return False
    state = await chat_repository.get_session_state(tenant)
    if not tenant_learn_enabled(state):
        return False
    cursor = await chat_repository.get_cursor(tenant, "expression")
    after = int(cursor.cursor_seq) if cursor else 0
    raw = (
        await chat_repository.messages_after(tenant, after, limit=_BATCH_SIZE)
        if messages is None
        else sorted(
            (message for message in messages if int(message.id or 0) > after), key=lambda message: int(message.id or 0)
        )
    )
    if len(raw) < _MIN_MESSAGES:
        return False
    seq_end = int(raw[-1].id or 0)
    clean = _clean_messages(raw, tenant)
    if not clean:
        return await chat_repository.save_expressions(
            tenant,
            [],
            {},
            cursor_seq=seq_end,
            expected_cursor=after,
            source_batch_id=expression_batch_id(int(raw[0].id or 0), seq_end),
        )
    selected = clean
    seq_start = int(selected[0].id or 0)
    batch_id = expression_batch_id(seq_start, seq_end)
    try:
        async with _lease(tenant, provider) as leased:
            items = await request_expressions(leased, selected)
            if items is None:
                return False
            if items and config.get("self_reflect", True):
                decisions = await request_review(
                    leased, [{"situation": item.situation, "style": item.style} for item in items]
                )
                items = [item for item, approved in zip(items, decisions) if approved]
            existing = await chat_repository.list_expressions(tenant, limit=1000)
            new_items, updates = merge_candidates(items, existing)
    except Exception as exc:
        logger.opt(exception=True).warning(f"[ChatExpression] 租户表达学习失败: {type(exc).__name__}")
        return False
    inserts: list[dict[str, Any]] = []
    for item in new_items:
        inserts.append(
            {
                "situation": item["situation"],
                "situation_hash": text_hash(item["situation"]),
                "style": item["style"],
                "style_hash": text_hash(item["style"]),
                "type": item["type"],
                "count": float(item["count"]),
                "checked": False,
                "rejected": False,
                "source_message_seq": int(item.get("source_message_seq") or seq_end),
                "learn_count": int(item["learn"]),
            }
        )
    return await chat_repository.save_expressions(
        tenant,
        inserts,
        updates,
        cursor_seq=seq_end,
        expected_cursor=after,
        source_batch_id=batch_id,
        evidence_ids=tuple(int(message.id or 0) for message in clean),
    )


async def select_expressions(
    tenant: str, query: str, rows: list[ChatExpression], *, history: list[dict[str, Any]] | None = None
) -> list[ChatExpression]:
    """向量模式召回后筛选；普通模式按 MaiBot 的计数权重抽样直接注入。"""
    active = [row for row in rows if not row.rejected]
    if len(active) < 10:
        return []
    vector_cfg = expression_vector_config()
    if vector_cfg.get("enabled"):
        try:
            pool = int(vector_cfg.get("candidate_pool_size", 50))
        except (TypeError, ValueError):
            pool = 50
        return await select_by_vectors(
            tenant,
            query,
            active,
            index_path=str(vector_cfg.get("index_path") or ""),
            pool_size=pool,
            history=history,
        )
    return sample_expression_candidates(active)


def weighted_sample(rows: list[ChatExpression], size: int) -> list[ChatExpression]:
    """移植 MaiBot 062d45c src/learners/weighted_sampling.py 的无放回抽样。"""
    remaining = list(rows)
    if len(remaining) <= size:
        return remaining
    selected: list[ChatExpression] = []
    for _ in range(min(size, len(remaining))):
        counts = [max(0.0, float(row.count or 0)) for row in remaining]
        lower, upper = min(counts), max(counts)
        weights = [1.0 if lower == upper else 1.0 + (value - lower) / (upper - lower) * 4 for value in counts]
        threshold = random.uniform(0, sum(weights))
        cumulative = 0.0
        for index, weight in enumerate(weights):
            cumulative += weight
            if threshold <= cumulative:
                selected.append(remaining.pop(index))
                break
    return selected


def sample_expression_candidates(rows: list[ChatExpression]) -> list[ChatExpression]:
    """对齐 MaisakaExpressionSelector 的普通候选池：不足 10 条不注入。"""
    if len(rows) < 10:
        return []
    frequent = [row for row in rows if float(row.count or 1) > 1]
    selected = weighted_sample(frequent, 5) if len(frequent) >= 10 else []
    selected.extend(weighted_sample(rows, 5))
    unique: dict[int, ChatExpression] = {}
    for row in selected:
        unique.setdefault(int(row.id or 0), row)
    return list(unique.values())


def expression_line(rows: list[ChatExpression]) -> str:
    """完整列出选中句式，沿用 MaiBot 的自由使用措辞。"""
    pairs = [
        f'- 当"{row.situation}"时，可以用"{row.style}"来表达。'
        for row in rows
        if str(row.situation or "").strip() and str(row.style or "").strip()
    ]
    if not pairs:
        return ""
    return "你可以视情况自由使用以下句式：\n" + "\n".join(pairs)
