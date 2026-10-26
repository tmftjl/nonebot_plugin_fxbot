"""表达学习：情境到说法的抽取、合并、复审与选择。"""

from __future__ import annotations

import json
import asyncio
import hashlib
from typing import Any
from contextlib import asynccontextmanager
from dataclasses import dataclass

from nonebot import logger
from rapidfuzz import fuzz

from .state import is_ready
from .config import (
    memory_config,
    expression_config,
    tenant_learn_enabled,
    expression_model_name,
    expression_learning_enabled,
)
from .models import ChatMessage, ChatExpression
from .extract import SENSITIVE
from ...config import get_manager as get_config_manager
from ..bindings import PROVIDER, get_binding
from .filtering import should_learn, sanitize_line
from ..providers import provider_manager
from .repository import TenantJobState, chat_repository

_BATCH_SIZE = 40
_MAX_ITEMS = 12
_COUNT_STEP = 0.006
_COUNT_CAP = 5.0
_MERGE_THRESHOLD = 75
_SELECT_THRESHOLD = 0.75
_EXPRESSION_TYPES = frozenset({"phrase", "tone", "structure", "emoji"})

_LEARN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"expressions": {"type": "array"}},
}
_REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"results": {"type": "array"}},
}
_SELECT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"selected_ids": {"type": "array"}},
}
_tick_lock: asyncio.Lock | None = None


@dataclass(frozen=True)
class ExpressionItem:
    """一条从批次中抽出的情境与说法。"""

    situation: str
    style: str
    type: str = "phrase"
    source_seq: int = 0


def normalize(text: object) -> str:
    """匹配与去重都使用同一份小写、空白归一化文本。"""
    return " ".join(str(text or "").casefold().split())


def text_hash(text: object) -> str:
    """情境与说法的稳定指纹。"""
    return hashlib.sha1(normalize(text).encode("utf-8")).hexdigest()


def expression_batch_id(seq_start: int, seq_end: int) -> str:
    """来源批次只由消息范围决定，撤回时据此失效。"""
    return f"expression:{int(seq_start)}:{int(seq_end)}"


def build_learn_prompt(messages: list[ChatMessage]) -> list[dict[str, Any]]:
    """构造表达抽取消息；只包含本批次可信的人类消息。"""
    lines = []
    for message in messages:
        name = message.sender_name or "用户"
        lines.append(f"[{int(message.id or 0)}] {name}({message.sender_id}): {message.content}")
    system = (
        "你是群聊表达分析器。只输出 JSON，不要解释。\n"
        "从给定的人类消息中提炼可以复用的说话方式：situation 是当时的情境或话题，"
        "style 是这个群里自然的说法或语气，type 取 phrase/tone/structure/emoji 之一。\n"
        "只写群成员真实使用或明显偏好的表达，不要编造，不要评价个人，"
        "不要包含联系方式、链接、密码、令牌、身份证、银行卡、手机号、住址。\n"
        f"最多 {_MAX_ITEMS} 条；situation 要能用来匹配当前话题，style 要短。\n"
        "如果这批消息没有可复用的表达，返回空数组。\n"
        '输出格式：{"expressions":[{"situation":"","style":"","type":"phrase","source_seq":1}]}'
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": "\n".join(lines)}]


def parse_expressions(data: object, messages: list[ChatMessage]) -> list[ExpressionItem] | None:
    """校验模型输出；无法解析返回 None，合法空结果返回空列表。"""
    if not isinstance(data, dict):
        return None
    raw_items = data.get("expressions")
    if not isinstance(raw_items, list):
        return None
    by_id = {int(message.id): message for message in messages if message.id is not None}
    items: list[ExpressionItem] = []
    seen: set[tuple[str, str]] = set()
    for raw in raw_items[:_MAX_ITEMS]:
        if not isinstance(raw, dict):
            continue
        situation = sanitize_line(raw.get("situation"), 60)
        style = sanitize_line(raw.get("style"), 60)
        if not situation or not style or SENSITIVE.search(situation) or SENSITIVE.search(style):
            continue
        key = (normalize(situation), normalize(style))
        if key in seen:
            continue
        seen.add(key)
        kind = str(raw.get("type") or "phrase").strip().lower()
        seq_text = str(raw.get("source_seq") or "").strip()
        source_seq = int(seq_text) if seq_text.isdigit() and int(seq_text) in by_id else 0
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
            data = await provider.chat_json(prompt, _LEARN_SCHEMA, model=model or None)
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


def build_review_prompt(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """构造一次批次复审提示；新条目只复审这一次。"""
    lines = [f"{index}. 情境：{item['situation']}；说法：{item['style']}" for index, item in enumerate(items, start=1)]
    system = (
        "你是表达复审器。判断每条“情境→说法”是否值得让机器人参考。\n"
        "值得：群内自然、可复用、不冒犯、不含隐私或广告。\n"
        "不值得：个人信息、攻击侮辱、广告、过于通用或生硬、依赖具体事实。\n"
        '只输出 JSON：{"results":[{"index":1,"approved":true}]}'
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": "\n".join(lines)}]


def parse_review(data: object, count: int) -> list[bool] | None:
    """把复审结果对齐到候选下标；缺项按不通过处理。"""
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        return None
    approved = [False] * max(0, count)
    for item in data["results"]:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        if 1 <= index <= count:
            approved[index - 1] = bool(item.get("approved"))
    return approved


async def request_review(provider: Any, items: list[dict[str, Any]], *, model: str = "") -> list[bool] | None:
    """调用复审模型；失败返回 None，由调用方决定是否保留未复审条目。"""
    if not items:
        return []
    prompt = build_review_prompt(items)
    for attempt in range(2):
        try:
            data = await provider.chat_json(prompt, _REVIEW_SCHEMA, model=model or None)
        except Exception as exc:
            logger.opt(exception=True).debug(f"[ChatExpression] 复审失败（第 {attempt + 1} 次）: {type(exc).__name__}")
            continue
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError:
                data = None
        parsed = parse_review(data, len(items))
        if parsed is not None:
            return parsed
    return None


def merge_candidates(
    items: list[ExpressionItem], rows: list[ChatExpression]
) -> tuple[list[dict[str, Any]], dict[int, tuple[float, int]]]:
    """把新条目并入相似表达；返回待插入条目与已有条目的计数更新。"""
    pool: list[dict[str, Any]] = [
        {
            "id": int(row.id or 0),
            "situation": row.situation,
            "style": row.style,
            "count": float(row.count),
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
                "learn": 0,
                "source_message_seq": item.source_seq,
                "merged": False,
                "new": True,
            }
            pool.append(target)
            new_items.append(target)
        target["count"] = min(_COUNT_CAP, float(target["count"]) + _COUNT_STEP)
        target["learn"] = int(target["learn"]) + 1
        target["merged"] = True
    updates = {
        int(row["id"]): (float(row["count"]), int(row["learn"])) for row in pool if not row["new"] and row["merged"]
    }
    return new_items, updates


def _best_match(item: ExpressionItem, pool: list[dict[str, Any]]) -> dict[str, Any] | None:
    """先按指纹精确匹配，再按情境与说法的模糊相似度合并。"""
    situation_hash = text_hash(item.situation)
    style_hash = text_hash(item.style)
    for row in pool:
        if text_hash(row["situation"]) == situation_hash and text_hash(row["style"]) == style_hash:
            return row
    needle_situation = normalize(item.situation)
    needle_style = normalize(item.style)
    best: dict[str, Any] | None = None
    best_score = (-1, -1.0)
    for row in pool:
        situation_score = fuzz.partial_ratio(needle_situation, normalize(row["situation"]))
        style_score = fuzz.partial_ratio(needle_style, normalize(row["style"]))
        if situation_score < _MERGE_THRESHOLD or style_score < _MERGE_THRESHOLD:
            continue
        score = (min(situation_score, style_score), (situation_score + style_score) / 2)
        if score > best_score:
            best = row
            best_score = score
    return best


def _clean_messages(messages: list[ChatMessage], tenant: str) -> list[ChatMessage]:
    """过滤命令、广告与无意义内容；保留重复口头禅。"""
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


@asynccontextmanager
async def _lease(tenant: str, provider: Any):
    """复用调用方 Provider，否则按会话绑定或全局默认租用。"""
    if provider is not None:
        yield provider
        return
    name = get_binding(PROVIDER, tenant)
    if not name:
        name = str(get_config_manager().get_system()["chat"].get("provider") or "")
    async with provider_manager.lease(name) as leased:
        yield leased


async def learn_expressions(tenant: str, *, provider: Any = None, cfg: dict[str, Any] | None = None) -> bool:
    """每 40 条人类消息学习一批；成功提交返回 True。"""
    if not is_ready() or not expression_learning_enabled():
        return False
    config = cfg or expression_config()
    if not config.get("enabled", True) or not config.get("learn", True):
        return False
    state = await chat_repository.get_session_state(tenant)
    if not tenant_learn_enabled(state):
        return False
    cursor = await chat_repository.get_cursor(tenant, "expression")
    after = int(cursor.cursor_seq) if cursor else 0
    raw = await chat_repository.messages_after(tenant, after, limit=_BATCH_SIZE)
    if len(raw) < _BATCH_SIZE:
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
    selected = _recent_budget(clean, int(memory_config()["max_input_chars_per_batch"]))
    seq_start = int(selected[0].id or 0)
    batch_id = expression_batch_id(seq_start, seq_end)
    model = expression_model_name()
    try:
        async with _lease(tenant, provider) as leased:
            items = await request_expressions(leased, selected, model=model)
            if items is None:
                return False
            existing = await chat_repository.list_expressions(tenant, limit=1000)
            new_items, updates = merge_candidates(items, existing)
            decisions = await request_review(leased, new_items, model=model) if new_items else []
    except Exception as exc:
        logger.opt(exception=True).warning(f"[ChatExpression] 租户表达学习失败: {type(exc).__name__}")
        return False
    inserts: list[dict[str, Any]] = []
    for index, item in enumerate(new_items):
        checked = decisions is not None
        approved = bool(decisions[index]) if checked else False
        inserts.append(
            {
                "situation": item["situation"],
                "situation_hash": text_hash(item["situation"]),
                "style": item["style"],
                "style_hash": text_hash(item["style"]),
                "type": item["type"],
                "count": float(item["count"]),
                "checked": checked,
                "rejected": checked and not approved,
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
    )


class FuzzySelector:
    """按情境相似度乘计数权重取 top-k。"""

    async def select(
        self, tenant: str, query: str, rows: list[ChatExpression], limit: int, *, provider: Any = None
    ) -> list[ChatExpression]:
        needle = normalize(query)
        if not needle or limit <= 0:
            return []
        scored: list[tuple[float, int, ChatExpression]] = []
        for row in rows:
            if not row.checked or row.rejected:
                continue
            similarity = fuzz.partial_ratio(needle, normalize(row.situation)) / 100
            if similarity < _SELECT_THRESHOLD:
                continue
            weight = min(float(row.count), _COUNT_CAP) / _COUNT_CAP
            if weight <= 0:
                continue
            scored.append((similarity * weight, int(row.id or 0), row))
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [row for _, _, row in scored[:limit]]


def build_select_prompt(query: str, candidates: list[ChatExpression]) -> list[dict[str, Any]]:
    """构造可选表达列表，交给 LLM 按当前语境挑选。"""
    lines = [f"{int(row.id or 0)}: 情境={row.situation} | 说法={row.style}" for row in candidates]
    system = (
        "你是表达选择器。根据当前聊天内容，从候选里挑选最自然、最贴合语境的表达。\n"
        "最多选 5 条；没有合适的就返回空数组。不要照抄无关或冒犯的说法。\n"
        '只输出 JSON：{"selected_ids":[1,2]}'
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "\n".join([*lines, f"\n当前内容：{query}"])},
    ]


def parse_selected_ids(data: object, candidates: list[ChatExpression], limit: int) -> list[int] | None:
    """校验 LLM 返回的 ID；重复与越界项丢弃。"""
    if not isinstance(data, dict) or not isinstance(data.get("selected_ids"), list):
        return None
    allowed = {int(row.id or 0) for row in candidates}
    selected: list[int] = []
    for raw in data["selected_ids"]:
        try:
            expression_id = int(raw)
        except (TypeError, ValueError):
            continue
        if expression_id in allowed and expression_id not in selected:
            selected.append(expression_id)
        if len(selected) >= limit:
            break
    return selected


class LlmSelector:
    """把候选交给 LLM 精排；默认关闭，失败时由调用方回退模糊匹配。"""

    def __init__(self, sample_size: int = 50) -> None:
        self.sample_size = max(1, int(sample_size))

    async def select(
        self, tenant: str, query: str, rows: list[ChatExpression], limit: int, *, provider: Any = None
    ) -> list[ChatExpression]:
        candidates = [row for row in rows if row.checked and not row.rejected][: self.sample_size]
        if not candidates or limit <= 0:
            return []
        prompt = build_select_prompt(query, candidates)
        async with _lease(tenant, provider) as leased:
            data = await leased.chat_json(prompt, _SELECT_SCHEMA, model=expression_model_name() or None)
        if isinstance(data, str):
            data = json.loads(data)
        selected = parse_selected_ids(data, candidates, limit)
        if selected is None:
            raise ValueError("表达选择结果格式错误")
        by_id = {int(row.id or 0): row for row in candidates}
        return [by_id[expression_id] for expression_id in selected]


async def select_expressions(
    tenant: str,
    query: str,
    rows: list[ChatExpression],
    cfg: dict[str, Any] | None = None,
    *,
    provider: Any = None,
) -> list[ChatExpression]:
    """按配置选择表达；LLM 失败时回退本地模糊匹配。"""
    config = cfg or expression_config()
    limit = max(1, int(config.get("inject_top_k", 5)))
    active = [row for row in rows if row.checked and not row.rejected]
    if not active:
        return []
    if config.get("llm_select"):
        try:
            return await LlmSelector(int(config.get("sample_size", 50))).select(
                tenant, query, active, limit, provider=provider
            )
        except Exception:
            logger.opt(exception=True).debug("[ChatExpression] LLM 选择失败，回退模糊匹配")
    return await FuzzySelector().select(tenant, query, active, limit)


def expression_line(rows: list[ChatExpression]) -> str:
    """把选中的表达压成一段参考，明确要求不要照抄。"""
    pairs = [
        f"当「{sanitize_line(row.situation, 60)}」时，可以说「{sanitize_line(row.style, 60)}」"
        for row in rows
        if str(row.situation or "").strip() and str(row.style or "").strip()
    ]
    if not pairs:
        return ""
    return sanitize_line(f"【表达参考】可以参考这些说法，不要照抄：{'；'.join(pairs)}", 600)


def _tick_guard() -> asyncio.Lock:
    global _tick_lock
    if _tick_lock is None:
        _tick_lock = asyncio.Lock()
    return _tick_lock


def _learn_enabled(candidate: TenantJobState, cfg: dict[str, Any]) -> bool:
    if candidate.learn_enabled is not None:
        return bool(candidate.learn_enabled)
    return bool(cfg["learn_enabled"])


async def expression_job_tick(*, provider: Any = None, cfg: dict[str, Any] | None = None) -> int:
    """按抽取间隔检查可学习租户；每轮最多提交 max_tenants_per_tick 个。"""
    if not is_ready() or not expression_learning_enabled():
        return 0
    config = cfg or expression_config()
    memory_cfg = memory_config()
    async with _tick_guard():
        try:
            snapshot = await chat_repository.scheduling_snapshot()
        except Exception:
            logger.opt(exception=True).debug("[ChatExpression] 读取表达学习租户失败")
            return 0
        processed = 0
        for candidate in sorted(snapshot, key=lambda item: item.last_message_id, reverse=True):
            if not _learn_enabled(candidate, memory_cfg):
                continue
            try:
                if await learn_expressions(candidate.tenant, provider=provider, cfg=config):
                    processed += 1
            except Exception:
                logger.opt(exception=True).warning(f"[ChatExpression] 租户表达学习失败: {candidate.tenant}")
            if processed >= int(memory_cfg["max_tenants_per_tick"]):
                break
        return processed
