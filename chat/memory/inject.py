"""按 MaiBot 的阶段边界注入 Planner 黑话参考和 Replyer 表达习惯。"""

from __future__ import annotations

from typing import Any

from nonebot import logger

from .state import is_ready
from .config import jargon_config, expression_config, tenant_learn_enabled
from .learning import TenantTarget, share_targets, share_tenants, tenant_target, learning_flags
from .filtering import sanitize_line
from .expression import expression_line, select_expressions
from .repository import chat_repository

_MAX_JARGON_HITS = 10
_STYLE_CHARS = 150
_JARGON_HEADER = "以下释义来自当前上下文中部分词汇的猜测含义，仅作理解聊天语境的参考，不一定为真，需要根据情景理解："


def _style_line(style: object) -> str:
    """仅注入用户明确设置的风格，保留完整的独立表达条目。"""
    summary = str(getattr(style, "manual_override", "") or "")
    return sanitize_line(f"【表达风格】{summary}", _STYLE_CHARS) if summary else ""


def context_user_texts(history: list[dict[str, Any]]) -> list[str]:
    """只取真实他人消息，排除自身发言、工具及内部通知并按消息 ID 去重。"""
    texts: list[str] = []
    seen: set[str] = set()
    for message in history:
        if (
            message.get("role") != "user"
            or message.get("_self_message")
            or message.get("_planner_internal")
            or message.get("_reference")
            or not message.get("_sender_id")
        ):
            continue
        identities = {str(value) for value in message.get("_source_ids", [])}
        if identities and identities.intersection(seen):
            continue
        seen.update(identities)
        content = message.get("content")
        if isinstance(content, list):
            content = " ".join(str(part.get("text") or "") for part in content if part.get("type") == "text")
        if str(content or "").strip():
            texts.append(str(content))
    return texts


def _jargon_hits(rows: list[object], texts: list[str], limit: int) -> list[object]:
    """沿用 MaiBot 的累计次数和首次命中位置评分，词条只保留一个作用域候选。"""
    matches: dict[str, tuple[float, int, object]] = {}
    for index, text in enumerate(texts):
        normalized_text = " ".join(text.casefold().split())
        for row in rows:
            term = " ".join(str(getattr(row, "normalized", "") or "").casefold().split())
            if not term or term in matches or not getattr(row, "meaning", "") or term not in normalized_text:
                continue
            score = float(getattr(row, "count", 0) or 0) - index * 0.01
            matches[term] = (score, index, row)
    ordered = sorted(matches, key=lambda term: (-matches[term][0], matches[term][1], -len(term), term))
    return [matches[term][2] for term in ordered[:limit]]


async def _related_tenants(tenant: str, target: TenantTarget | None, groups: object) -> list[str]:
    """共享组内的租户；未配置共享时只返回当前租户。"""
    targets = share_targets(groups, target)
    if not targets:
        return [tenant]
    known = await chat_repository.list_tenants()
    return share_tenants(tenant, target, targets, known)


async def learning_context(
    tenant: str,
    query: str = "",
    *,
    phase: str = "reply",
    history: list[dict[str, Any]] | None = None,
    references: dict[str, str] | None = None,
    trace: dict[str, Any] | None = None,
) -> str:
    """Planner 匹配上下文黑话；Replyer 独立选择表达，不再共享短查询和总字数预算。"""
    if not is_ready() or not tenant_learn_enabled(await chat_repository.get_session_state(tenant)):
        return ""
    target = tenant_target(tenant)
    injected: dict[str, Any] = {"style": 0, "jargon": [], "expressions": []}
    sections: list[str] = []
    try:
        if phase == "planner":
            cfg = jargon_config()
            use, _ = learning_flags(cfg.get("learning_list"), target)
            if not use:
                return ""
            tenants = await _related_tenants(tenant, target, cfg.get("groups"))
            rows = await chat_repository.list_jargon(tenant, status="active", limit=None, tenants=tenants)
            hits = _jargon_hits(rows, context_user_texts(history or []), _MAX_JARGON_HITS)
            existing = references if references is not None else {}
            new_hits = [row for row in hits if str(getattr(row, "normalized", "")) not in existing]
            for row in new_hits:
                existing[str(getattr(row, "normalized", ""))] = (
                    f"{getattr(row, 'term', '')}：{getattr(row, 'meaning', '')}"
                )
            if existing:
                sections.append("\n".join([_JARGON_HEADER, *existing.values()]))
            injected["jargon"] = [line.split("：", 1)[0] for line in existing.values()]
            if new_hits:
                await chat_repository.mark_jargon_used(
                    tenant, [str(getattr(row, "term", "")) for row in new_hits], tenants=tenants
                )
        else:
            cfg = expression_config()
            use, _ = learning_flags(cfg.get("learning_list"), target)
            if not use:
                return ""
            style = await chat_repository.get_style(tenant)
            style_text = _style_line(style)
            if style_text:
                sections.append(style_text)
                injected["style"] = int(getattr(style, "id", 0) or 0)
                await chat_repository.mark_style_used(tenant)
            tenants = await _related_tenants(tenant, target, cfg.get("groups"))
            rows = await chat_repository.list_expressions(
                tenant,
                status="active",
                limit=None,
                tenants=tenants,
                manual_only=bool(cfg.get("checked_only", False)),
            )
            selected = await select_expressions(tenant, query, rows, history=history)
            text = expression_line(selected)
            if text:
                sections.append(text)
                injected["expressions"] = [int(row.id) for row in selected]
                await chat_repository.mark_expressions_used(tenant, injected["expressions"], tenants=tenants)
    except Exception:
        logger.opt(exception=True).debug("[ChatLearning] 构造学习参考失败")
    block = "\n".join(sections)
    if trace is not None:
        injected["chars"] = {
            "style": len(sections[0]) if injected["style"] and sections else 0,
            "jargon": len(block) if phase == "planner" else 0,
            "expressions": len(sections[-1]) if injected["expressions"] else 0,
            "total": len(block),
        }
        trace["injected"] = injected
    return block
