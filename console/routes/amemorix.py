"""A_Memorix 长期记忆控制台。

所有列表都按 FxBot 租户（即 A_Memorix 的 ``chat_id``）过滤，不把别的群的
段落、关系、事实混进来。画像与人物记录来自 ``chat_person``。
"""

from __future__ import annotations

import re
import json
import uuid
import base64
import binascii
from typing import Any
from pathlib import Path
from datetime import datetime, timezone

from fastapi import Depends, APIRouter, HTTPException
from nonebot import logger
from sqlmodel import select
from fastapi.responses import FileResponse

from ...db import base_models
from ..auth import sse_auth, bearer_auth
from ...utils.tz import SHANGHAI_TZ
from ...chat.amemorix.models import ChatPerson
from ...chat.amemorix.runtime import amemorix_runtime
from ...chat.amemorix._host.host_config import save_amemorix_config

router = APIRouter(prefix="/amemorix", tags=["fxbot-amemorix"], dependencies=[Depends(bearer_auth)])
file_router = APIRouter(prefix="/amemorix", tags=["fxbot-amemorix"], dependencies=[Depends(sse_auth)])

_RECORD_TYPES = ("paragraph", "entity", "relation", "episode", "fact")
_SOURCE_KIND_PREFIXES = (
    ("chat_summary:", "聊天摘要"),
    ("person_fact:", "人物事实"),
    ("chat_stream:", "聊天流"),
)


def _tenant(value: str) -> str:
    tenant = str(value or "").strip()
    if not tenant:
        raise HTTPException(status_code=400, detail="tenant 不能为空")
    return tenant


def _unavailable() -> HTTPException:
    detail = amemorix_runtime.error or "A_Memorix 未启用或内核尚未就绪"
    return HTTPException(status_code=503, detail=detail)


async def _kernel() -> Any:
    kernel = await amemorix_runtime.get_kernel()
    if kernel is None or not amemorix_runtime.available:
        raise _unavailable()
    return kernel


def _metadata(kernel: Any) -> Any:
    store = getattr(kernel, "metadata_store", None)
    if store is None:
        raise _unavailable()
    return store


def _iso(value: Any) -> str | None:
    try:
        stamp = float(value)
    except (TypeError, ValueError):
        return None
    if stamp <= 0:
        return None
    return datetime.fromtimestamp(stamp, tz=timezone.utc).astimezone(SHANGHAI_TZ).isoformat()


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _json(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError:
            return fallback
    if isinstance(value, str) and value.strip():
        try:
            return json.loads(value)
        except ValueError:
            return fallback
    return fallback


def _trim(value: Any, limit: int = 160) -> str:
    text = str(value or "").strip()
    return text[:limit] + ("..." if len(text) > limit else "")


def _like(value: str) -> str:
    escaped = str(value or "").strip().lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _tenant_clause(alias: str) -> str:
    """段落归属租户：metadata.chat_id 或 chat_summary 来源。"""
    return (
        f"((CASE WHEN json_valid({alias}.metadata) THEN json_extract({alias}.metadata, '$.chat_id') END) = ?"
        f" OR {alias}.source = ?)"
    )


def _tenant_params(tenant: str) -> list[Any]:
    return [tenant, f"chat_summary:{tenant}"]


def _source_label(source: str, tenant: str, person_names: dict[str, str]) -> str:
    token = str(source or "").strip()
    if not token:
        return ""
    if token == f"chat_summary:{tenant}":
        return "聊天摘要"
    if token.startswith("person_fact:"):
        return person_names.get(token.removeprefix("person_fact:").strip()) or "人物事实"
    for prefix, label in _SOURCE_KIND_PREFIXES:
        if token.startswith(prefix):
            return label
    return token


def _person_names(persons: list[dict[str, Any]]) -> dict[str, str]:
    return {item["person_id"]: item["person_name"] or item["user_nickname"] or item["user_id"] for item in persons}


async def _persons(tenant: str) -> list[dict[str, Any]]:
    if base_models.async_maker is None:
        return []
    statement = select(ChatPerson).where(ChatPerson.tenant == tenant).order_by(ChatPerson.updated_at.desc())
    async with base_models.async_maker() as session:
        rows = (await session.execute(statement)).scalars().all()
    return [
        {
            "person_id": row.person_id,
            "user_id": row.user_id,
            "platform": row.platform,
            "person_name": row.person_name,
            "user_nickname": row.user_nickname,
            "group_cardname": _json(row.group_cardname, []),
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }
        for row in rows
    ]


def _paragraph_payload(row: dict[str, Any], tenant: str, person_names: dict[str, str]) -> dict[str, Any]:
    content = str(row.get("content") or "").strip()
    source = str(row.get("source") or "").strip()
    metadata = _json(row.get("metadata"), {})
    return {
        "type": "paragraph",
        "id": str(row.get("hash") or ""),
        "title": _trim(content, 88) or source or "未命名段落",
        "summary": _trim(content, 240),
        "source": source,
        "source_label": _source_label(source, tenant, person_names),
        "status": "deleted" if int(row.get("is_deleted") or 0) else "active",
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("updated_at")),
        "metadata": {
            "knowledge_type": str(row.get("knowledge_type") or "mixed"),
            "word_count": int(row.get("word_count") or 0),
            "vector_indexed": row.get("vector_index") is not None,
            "raw": metadata if isinstance(metadata, dict) else {},
        },
    }


def _entity_payload(row: dict[str, Any], tenant: str, person_names: dict[str, str]) -> dict[str, Any]:
    name = str(row.get("name") or row.get("hash") or "").strip()
    count = int(row.get("active_evidence_count") or 0)
    return {
        "type": "entity",
        "id": str(row.get("hash") or ""),
        "title": name,
        "summary": f"由 {count} 条段落支撑",
        "source": "",
        "source_label": "",
        "status": "deleted" if int(row.get("is_deleted") or 0) else "active",
        "created_at": _iso(row.get("created_at")),
        "updated_at": None,
        "metadata": {
            "name": name,
            "appearance_count": int(row.get("appearance_count") or 0),
            "active_evidence_count": count,
            "vector_indexed": row.get("vector_index") is not None,
        },
    }


def _relation_payload(row: dict[str, Any], tenant: str, person_names: dict[str, str]) -> dict[str, Any]:
    subject = str(row.get("subject") or "").strip()
    predicate = str(row.get("predicate") or "").strip()
    obj = str(row.get("object") or "").strip()
    origin = str(row.get("origin_source") or "").strip()
    return {
        "type": "relation",
        "id": str(row.get("hash") or ""),
        "title": " ".join(item for item in (subject, predicate, obj) if item),
        "summary": f"置信度 {float(row.get('confidence') or 0.0):.2f}",
        "source": origin,
        "source_label": _source_label(origin, tenant, person_names),
        "status": "inactive" if int(row.get("is_inactive") or 0) else "active",
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("last_reinforced")),
        "metadata": {
            "subject": subject,
            "predicate": predicate,
            "object": obj,
            "confidence": float(row.get("confidence") or 0.0),
            "vector_state": str(row.get("vector_state") or "none"),
            "is_pinned": bool(int(row.get("is_pinned") or 0)),
        },
    }


def _episode_payload(row: dict[str, Any], tenant: str, person_names: dict[str, str]) -> dict[str, Any]:
    source = str(row.get("source") or "").strip()
    return {
        "type": "episode",
        "id": str(row.get("episode_id") or ""),
        "title": str(row.get("title") or "").strip() or "未命名 Episode",
        "summary": _trim(row.get("summary"), 240),
        "source": source,
        "source_label": _source_label(source, tenant, person_names),
        "status": "active",
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("updated_at")),
        "metadata": {
            "event_time_start": _float(row.get("event_time_start")),
            "event_time_end": _float(row.get("event_time_end")),
            "paragraph_count": int(row.get("paragraph_count") or 0),
            "keywords": _json(row.get("keywords_json"), []),
            "participants": _json(row.get("participants_json"), []),
        },
    }


def _fact_payload(row: dict[str, Any], tenant: str, person_names: dict[str, str]) -> dict[str, Any]:
    fact_key = str(row.get("fact_key") or "").strip()
    value_text = str(row.get("value_text") or "").strip()
    readable_key = "" if not fact_key or fact_key.startswith("statement:") else fact_key
    scope_type = str(row.get("scope_type") or "").strip()
    scope_id = str(row.get("scope_id") or "").strip()
    scope_label = person_names.get(scope_id, "") if scope_type == "person" else tenant
    scope_type_label = {"person": "人物", "chat": "聊天流"}.get(scope_type, scope_type)
    if readable_key and value_text:
        title = f"{readable_key}: {value_text}"
    else:
        title = value_text or readable_key or "未命名事实"
    return {
        "type": "fact",
        "id": str(row.get("claim_id") or ""),
        "title": title,
        "summary": f"{scope_type_label} · {scope_label}" if scope_label else scope_type_label,
        "source": "",
        "source_label": "",
        "status": str(row.get("status") or "active"),
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("updated_at") or row.get("last_confirmed_at")),
        "metadata": {
            "scope_type": scope_type,
            "scope_id": scope_id,
            "fact_key": fact_key,
            "value_text": value_text,
            "polarity": str(row.get("polarity") or ""),
            "cardinality": str(row.get("cardinality") or ""),
            "stability": str(row.get("stability") or ""),
            "profile_section": str(row.get("profile_section") or ""),
            "authority": str(row.get("authority") or ""),
            "confidence": float(row.get("confidence") or 0.0),
            "valid_from": _iso(row.get("valid_from")),
            "valid_to": _iso(row.get("valid_to")),
        },
    }


def _record_payload(record_type: str, row: dict[str, Any], tenant: str, person_names: dict[str, str]) -> dict[str, Any]:
    builders = {
        "paragraph": _paragraph_payload,
        "entity": _entity_payload,
        "relation": _relation_payload,
        "episode": _episode_payload,
        "fact": _fact_payload,
    }
    return builders[record_type](row, tenant, person_names)


def _types(value: str) -> list[str]:
    requested = [item.strip().lower() for item in str(value or "").split(",") if item.strip()]
    if not requested:
        return list(_RECORD_TYPES)
    unknown = sorted(set(requested) - set(_RECORD_TYPES))
    if unknown:
        raise HTTPException(status_code=400, detail=f"不支持的记忆类型: {', '.join(unknown)}")
    return list(dict.fromkeys(requested))


def _scope_sql(tenant: str, person_ids: list[str]) -> tuple[str, list[Any]]:
    sql = "(f.scope_type = 'chat' AND f.scope_id = ?"
    params: list[Any] = [tenant]
    if person_ids:
        sql += " OR (f.scope_type = 'person' AND f.scope_id IN (" + ",".join("?" for _ in person_ids) + "))"
        params.extend(person_ids)
    return sql + ")", params


def _query(store: Any, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    try:
        return [dict(row) for row in store.query(sql, params)]
    except Exception as exc:
        logger.opt(exception=True).warning("[A_Memorix] 控制台查询失败")
        raise HTTPException(status_code=500, detail=f"长期记忆查询失败: {exc}") from exc


def _limit(value: Any, default: int = 50, maximum: int = 300) -> int:
    try:
        size = int(value)
    except (TypeError, ValueError):
        size = default
    return max(1, min(maximum, size))


def _label(value: Any, limit: int = 100) -> str:
    text = str(value or "").strip().replace("\n", " ")
    return text[:limit] + ("..." if len(text) > limit else "")


def _tenant_hashes(store: Any, tenant: str) -> tuple[set[str], set[str], set[str]]:
    """当前租户的段落、实体、关系哈希，用于判断全局操作是否触及本租户。"""
    clause = _tenant_clause("p")
    params = tuple(_tenant_params(tenant))
    paragraphs = {
        str(row.get("hash") or "") for row in _query(store, f"SELECT p.hash FROM paragraphs p WHERE {clause}", params)
    }
    entities = {
        str(row.get("entity_hash") or "")
        for row in _query(
            store,
            "SELECT DISTINCT pe.entity_hash FROM paragraph_entities pe "
            f"JOIN paragraphs p ON p.hash=pe.paragraph_hash WHERE {clause}",
            params,
        )
    }
    relations = {
        str(row.get("relation_hash") or "")
        for row in _query(
            store,
            "SELECT DISTINCT pr.relation_hash FROM paragraph_relations pr "
            f"JOIN paragraphs p ON p.hash=pr.paragraph_hash WHERE {clause}",
            params,
        )
    }
    return paragraphs, entities, relations


def _operation_items(operation: dict[str, Any], hashes: tuple[set[str], set[str], set[str]]) -> list[dict[str, Any]]:
    paragraphs, entities, relations = hashes
    matched = []
    for item in operation.get("items") or []:
        kind = str(item.get("item_type") or "").strip()
        token = str(item.get("item_hash") or "").strip()
        if (
            (kind == "paragraph" and token in paragraphs)
            or (kind == "entity" and token in entities)
            or (kind == "relation" and token in relations)
        ):
            matched.append(item)
    return matched


def _plan_candidates(plan: dict[str, Any]) -> list[dict[str, str]]:
    """修正计划里由控制台勾选产生的候选记录。"""
    preview = plan.get("preview") if isinstance(plan.get("preview"), dict) else {}
    items: list[dict[str, str]] = []
    for candidate in preview.get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        kind = str(candidate.get("target_type") or "").strip()
        token = str(candidate.get("hash") or "").strip()
        if kind in {"paragraph", "relation"} and token:
            items.append({"type": kind, "id": token})
    return items


def _tenant_owns_plan(plan: dict[str, Any], tenant: str, hashes: tuple[set[str], set[str], set[str]]) -> bool:
    """计划按聊天流归属，或全部候选记录都属于当前租户。"""
    if str(plan.get("target_chat_id") or "") == tenant:
        return True
    candidates = _plan_candidates(plan)
    if not candidates:
        return False
    paragraphs, _, relations = hashes
    return all(item["id"] in (paragraphs if item["type"] == "paragraph" else relations) for item in candidates)


def _item_preview(item: dict[str, Any]) -> dict[str, Any]:
    kind = str(item.get("item_type") or "").strip()
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    if kind == "paragraph":
        paragraph = payload.get("paragraph") if isinstance(payload.get("paragraph"), dict) else {}
        return {"type": kind, "label": _label(paragraph.get("content"), 120)}
    if kind == "entity":
        entity = payload.get("entity") if isinstance(payload.get("entity"), dict) else {}
        return {"type": kind, "label": _label(entity.get("name"), 120)}
    if kind == "relation":
        relation = payload.get("relation") if isinstance(payload.get("relation"), dict) else {}
        label = " ".join(str(relation.get(key) or "").strip() for key in ("subject", "predicate", "object"))
        return {"type": kind, "label": _label(label, 120)}
    return {"type": kind, "label": str(item.get("item_key") or item.get("item_hash") or "")}


def _mode_label(mode: Any) -> str:
    return {"paragraph": "段落", "entity": "实体", "relation": "关系"}.get(str(mode or ""), "记忆")


def _decode_base64(value: Any, *, field: str) -> bytes:
    raw = str(value or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail=f"{field} 不能为空")
    try:
        return base64.b64decode(raw, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(status_code=400, detail=f"{field} 不是合法 Base64") from exc


def _safe_file_name(value: Any, suffix: str) -> str:
    stem = re.sub(r"[^0-9A-Za-z._\-一-鿿]+", "-", Path(str(value or "")).stem).strip(".-")
    return f"{(stem or 'upload')[:80]}{suffix}"


def _kernel_path(kernel: Any, *parts: str) -> Path:
    path = Path(kernel.data_dir).joinpath(*parts)
    path.mkdir(parents=True, exist_ok=True)
    return path


@router.get("/status")
async def status(tenant: str) -> dict[str, Any]:
    """内核状态与当前租户的记忆计数。"""
    tenant = _tenant(tenant)
    persons = await _persons(tenant)
    payload: dict[str, Any] = {
        "available": amemorix_runtime.available,
        "enabled": bool(amemorix_runtime.config().plugin.enabled),
        "error": amemorix_runtime.error,
        "counts": {
            "paragraphs": 0,
            "entities": 0,
            "relations": 0,
            "episodes": 0,
            "facts": 0,
            "persons": len(persons),
            "profiles": 0,
        },
        "runtime": {},
    }
    integration = amemorix_runtime.config().integration
    payload["integration"] = {
        "enable_memory_query_tool": integration.enable_memory_query_tool,
        "enable_person_profile_query_tool": integration.enable_person_profile_query_tool,
        "enable_person_profile_injection": integration.enable_person_profile_injection,
        "heuristic_memory_recall_enabled": integration.heuristic_memory_recall_enabled,
    }
    if not amemorix_runtime.available:
        return payload
    kernel = await _kernel()
    store = _metadata(kernel)
    clause = _tenant_clause("p")
    tenant_params = tuple(_tenant_params(tenant))
    person_ids = [item["person_id"] for item in persons]
    scope_sql, scope_params = _scope_sql(tenant, person_ids)

    counts = payload["counts"]
    counts["paragraphs"] = _count(
        store,
        f"SELECT COUNT(*) AS n FROM paragraphs p WHERE COALESCE(p.is_deleted,0)=0 AND {clause}",
        tenant_params,
    )
    counts["entities"] = _count(
        store,
        "SELECT COUNT(*) AS n FROM entities e WHERE COALESCE(e.is_deleted,0)=0 AND EXISTS ("
        f"SELECT 1 FROM paragraph_entities pe JOIN paragraphs p ON p.hash=pe.paragraph_hash "
        f"WHERE pe.entity_hash=e.hash AND COALESCE(p.is_deleted,0)=0 AND {clause})",
        tenant_params,
    )
    counts["relations"] = _count(
        store,
        "SELECT COUNT(*) AS n FROM relations r WHERE COALESCE(r.is_inactive,0)=0 AND EXISTS ("
        f"SELECT 1 FROM paragraph_relations pr JOIN paragraphs p ON p.hash=pr.paragraph_hash "
        f"WHERE pr.relation_hash=r.hash AND COALESCE(p.is_deleted,0)=0 AND {clause})",
        tenant_params,
    )
    counts["episodes"] = _count(
        store,
        "SELECT COUNT(*) AS n FROM episodes e WHERE EXISTS ("
        f"SELECT 1 FROM episode_paragraphs ep JOIN paragraphs p ON p.hash=ep.paragraph_hash "
        f"WHERE ep.episode_id=e.episode_id AND COALESCE(p.is_deleted,0)=0 AND {clause})",
        tenant_params,
    )
    counts["facts"] = _count(
        store,
        f"SELECT COUNT(*) AS n FROM fact_claims f WHERE LOWER(COALESCE(f.status,'active'))='active' AND {scope_sql}",
        tuple(scope_params),
    )
    if person_ids:
        placeholders = ",".join("?" for _ in person_ids)
        counts["profiles"] = _count(
            store,
            f"SELECT COUNT(DISTINCT person_id) AS n FROM person_profile_snapshots WHERE person_id IN ({placeholders})",
            tuple(person_ids),
        )
    try:
        runtime = await kernel.memory_runtime_admin(action="get_config")
        keys = (
            "data_dir",
            "embedding_dimension",
            "stored_vector_dimension",
            "vector_rebuild_required",
            "vector_rebuild_message",
            "embedding_degraded",
            "embedding_degraded_reason",
            "relation_vectors_enabled",
            "vector_pools_ready",
            "vector_pools_effective_mode",
            "paragraph_vector_backfill_pending",
            "paragraph_vector_backfill_failed",
            "auto_save",
        )
        payload["runtime"] = {key: runtime.get(key) for key in keys if key in runtime}
    except Exception as exc:
        payload["runtime"] = {"error": f"{type(exc).__name__}: {exc}"}
    return payload


def _count(store: Any, sql: str, params: tuple[Any, ...]) -> int:
    rows = _query(store, sql, params)
    return int(rows[0].get("n") or 0) if rows else 0


@router.get("/records")
async def search_records(
    tenant: str,
    query: str = "",
    types: str = "",
    limit: int = 50,
    include_inactive: bool = False,
) -> dict[str, Any]:
    """检索当前租户的段落、实体、关系、片段与事实。"""
    tenant = _tenant(tenant)
    selected = _types(types)
    keyword = str(query or "").strip()
    pattern = _like(keyword)
    size = max(1, min(200, int(limit)))
    kernel = await _kernel()
    store = _metadata(kernel)
    persons = await _persons(tenant)
    names = _person_names(persons)
    rows_by_type: dict[str, list[dict[str, Any]]] = {}
    clause = _tenant_clause("p")
    scope_sql, scope_params = _scope_sql(tenant, [item["person_id"] for item in persons])
    inactive = 1 if include_inactive else 0

    if "paragraph" in selected:
        rows_by_type["paragraph"] = _query(
            store,
            "SELECT p.hash, p.content, p.source, p.knowledge_type, p.word_count, p.vector_index, "
            "p.created_at, p.updated_at, p.metadata, p.is_deleted FROM paragraphs p "
            f"WHERE (? = 1 OR COALESCE(p.is_deleted,0)=0) AND {clause} AND ("
            "? = '' OR LOWER(COALESCE(p.content,'')) LIKE ? ESCAPE '\\' "
            "OR LOWER(COALESCE(p.hash,'')) LIKE ? ESCAPE '\\' OR LOWER(COALESCE(p.source,'')) LIKE ? ESCAPE '\\') "
            "ORDER BY COALESCE(p.updated_at, p.created_at, 0) DESC LIMIT ?",
            (inactive, *_tenant_params(tenant), keyword, pattern, pattern, pattern, size),
        )
    if "entity" in selected:
        rows_by_type["entity"] = _query(
            store,
            "SELECT e.hash, e.name, e.appearance_count, e.vector_index, e.created_at, e.metadata, e.is_deleted, "
            "(SELECT COUNT(DISTINCT pe2.paragraph_hash) FROM paragraph_entities pe2 "
            " JOIN paragraphs p2 ON p2.hash=pe2.paragraph_hash WHERE pe2.entity_hash=e.hash "
            f" AND COALESCE(p2.is_deleted,0)=0 AND {clause.replace('p.', 'p2.')}) AS active_evidence_count "
            "FROM entities e WHERE (? = 1 OR COALESCE(e.is_deleted,0)=0) AND EXISTS ("
            "SELECT 1 FROM paragraph_entities pe JOIN paragraphs p ON p.hash=pe.paragraph_hash "
            f"WHERE pe.entity_hash=e.hash AND COALESCE(p.is_deleted,0)=0 AND {clause}) "
            "AND (? = '' OR LOWER(COALESCE(e.name,'')) LIKE ? ESCAPE '\\' "
            "OR LOWER(COALESCE(e.hash,'')) LIKE ? ESCAPE '\\') "
            "ORDER BY e.appearance_count DESC, e.created_at DESC LIMIT ?",
            (*_tenant_params(tenant), inactive, *_tenant_params(tenant), keyword, pattern, pattern, size),
        )
    if "relation" in selected:
        rows_by_type["relation"] = _query(
            store,
            "SELECT r.hash, r.subject, r.predicate, r.object, r.confidence, r.source_paragraph, r.vector_state, "
            "r.created_at, r.last_reinforced, r.is_inactive, r.is_pinned, r.metadata, p.source AS origin_source "
            "FROM relations r LEFT JOIN paragraphs p ON p.hash=r.source_paragraph "
            "WHERE (? = 1 OR COALESCE(r.is_inactive,0)=0) AND EXISTS ("
            "SELECT 1 FROM paragraph_relations pr JOIN paragraphs p2 ON p2.hash=pr.paragraph_hash "
            f"WHERE pr.relation_hash=r.hash AND COALESCE(p2.is_deleted,0)=0 AND {clause.replace('p.', 'p2.')}) "
            "AND (? = '' OR LOWER(COALESCE(r.subject,'')) LIKE ? ESCAPE '\\' "
            "OR LOWER(COALESCE(r.predicate,'')) LIKE ? ESCAPE '\\' "
            "OR LOWER(COALESCE(r.object,'')) LIKE ? ESCAPE '\\' OR LOWER(COALESCE(r.hash,'')) LIKE ? ESCAPE '\\') "
            "ORDER BY COALESCE(r.last_reinforced, r.created_at, 0) DESC LIMIT ?",
            (inactive, *_tenant_params(tenant), keyword, pattern, pattern, pattern, pattern, size),
        )
    if "episode" in selected:
        rows_by_type["episode"] = _query(
            store,
            "SELECT e.episode_id, e.source, e.title, e.summary, e.paragraph_count, e.event_time_start, "
            "e.event_time_end, e.participants_json, e.keywords_json, e.created_at, e.updated_at FROM episodes e "
            "WHERE EXISTS (SELECT 1 FROM episode_paragraphs ep JOIN paragraphs p ON p.hash=ep.paragraph_hash "
            f"WHERE ep.episode_id=e.episode_id AND COALESCE(p.is_deleted,0)=0 AND {clause}) "
            "AND (? = '' OR LOWER(COALESCE(e.title,'')) LIKE ? ESCAPE '\\' "
            "OR LOWER(COALESCE(e.summary,'')) LIKE ? ESCAPE '\\' "
            "OR LOWER(COALESCE(e.keywords_json,'')) LIKE ? ESCAPE '\\' "
            "OR LOWER(COALESCE(e.participants_json,'')) LIKE ? ESCAPE '\\' "
            "OR LOWER(COALESCE(e.episode_id,'')) LIKE ? ESCAPE '\\' "
            "OR LOWER(COALESCE(e.source,'')) LIKE ? ESCAPE '\\') "
            "ORDER BY COALESCE(e.updated_at, e.event_time_end, e.created_at, 0) DESC LIMIT ?",
            (*_tenant_params(tenant), keyword, pattern, pattern, pattern, pattern, pattern, pattern, size),
        )
    if "fact" in selected:
        rows_by_type["fact"] = _query(
            store,
            "SELECT f.* FROM fact_claims f "
            f"WHERE (? = 1 OR LOWER(COALESCE(f.status,'active'))='active') AND {scope_sql} "
            "AND (? = '' OR LOWER(COALESCE(f.fact_key,'')) LIKE ? ESCAPE '\\' "
            "OR LOWER(COALESCE(f.value_text,'')) LIKE ? ESCAPE '\\' "
            "OR LOWER(COALESCE(f.scope_id,'')) LIKE ? ESCAPE '\\' "
            "OR LOWER(COALESCE(f.claim_id,'')) LIKE ? ESCAPE '\\') "
            "ORDER BY COALESCE(f.updated_at, f.last_confirmed_at, f.created_at, 0) DESC LIMIT ?",
            (inactive, *scope_params, keyword, pattern, pattern, pattern, pattern, size),
        )

    items = [_record_payload(kind, row, tenant, names) for kind, rows in rows_by_type.items() for row in rows]
    items.sort(key=lambda item: item.get("updated_at") or item.get("created_at") or "", reverse=True)
    items = items[:size]
    return {
        "success": True,
        "query": keyword,
        "types": selected,
        "include_inactive": include_inactive,
        "limit": size,
        "count": len(items),
        "counts": {kind: sum(1 for item in items if item["type"] == kind) for kind in selected},
        "items": items,
    }


def _detail_row(store: Any, record_type: str, record_id: str) -> dict[str, Any]:
    token = str(record_id or "").strip()
    if record_type == "paragraph":
        sql = "SELECT * FROM paragraphs WHERE hash = ? LIMIT 1"
        params: tuple[Any, ...] = (token,)
    elif record_type == "entity":
        sql = "SELECT * FROM entities WHERE hash = ? OR LOWER(TRIM(name)) = LOWER(TRIM(?)) LIMIT 1"
        params = (token, token)
    elif record_type == "relation":
        sql = "SELECT * FROM relations WHERE hash = ? LIMIT 1"
        params = (token,)
    elif record_type == "episode":
        sql = "SELECT * FROM episodes WHERE episode_id = ? LIMIT 1"
        params = (token,)
    else:
        sql = "SELECT * FROM fact_claims WHERE claim_id = ? LIMIT 1"
        params = (token,)
    rows = _query(store, sql, params)
    if not rows:
        raise HTTPException(status_code=404, detail="记忆记录不存在")
    return rows[0]


def _related_paragraphs(
    store: Any,
    record_type: str,
    row: dict[str, Any],
    tenant: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    clause = _tenant_clause("p")
    base = (
        "SELECT DISTINCT p.hash, p.content, p.source, p.created_at FROM paragraphs p "
        f"WHERE COALESCE(p.is_deleted,0)=0 AND {clause}"
    )
    tenant_params = tuple(_tenant_params(tenant))
    if record_type == "paragraph":
        rows = _query(store, base + " AND p.hash = ? LIMIT ?", (*tenant_params, str(row.get("hash") or ""), limit))
    elif record_type == "entity":
        rows = _query(
            store,
            base + " AND p.hash IN (SELECT paragraph_hash FROM paragraph_entities WHERE entity_hash = ?) LIMIT ?",
            (*tenant_params, str(row.get("hash") or ""), limit),
        )
    elif record_type == "relation":
        rows = _query(
            store,
            base + " AND p.hash IN (SELECT paragraph_hash FROM paragraph_relations WHERE relation_hash = ?) LIMIT ?",
            (*tenant_params, str(row.get("hash") or ""), limit),
        )
    elif record_type == "episode":
        rows = _query(
            store,
            base + " AND p.hash IN (SELECT paragraph_hash FROM episode_paragraphs WHERE episode_id = ?) LIMIT ?",
            (*tenant_params, str(row.get("episode_id") or ""), limit),
        )
    else:
        rows = []
    return [
        {
            "hash": str(item.get("hash") or ""),
            "content": str(item.get("content") or ""),
            "source": str(item.get("source") or ""),
            "created_at": _iso(item.get("created_at")),
        }
        for item in rows
    ]


@router.get("/records/{record_type}/{record_id}")
async def record_detail(record_type: str, record_id: str, tenant: str) -> dict[str, Any]:
    """单条记忆详情与相关原文。"""
    tenant = _tenant(tenant)
    kind = record_type.strip().lower()
    if kind not in _RECORD_TYPES:
        raise HTTPException(status_code=400, detail=f"不支持的记忆类型: {record_type}")
    kernel = await _kernel()
    store = _metadata(kernel)
    row = _detail_row(store, kind, record_id)
    persons = await _persons(tenant)
    names = _person_names(persons)
    return {
        "success": True,
        "record": _record_payload(kind, row, tenant, names),
        "detail": row,
        "paragraphs": _related_paragraphs(store, kind, row, tenant),
    }


@router.delete("/records/{record_type}/{record_id}")
async def delete_record(record_type: str, record_id: str, tenant: str) -> dict[str, Any]:
    """删除段落/实体/关系，或撤回事实。"""
    tenant = _tenant(tenant)
    kind = record_type.strip().lower()
    token = str(record_id or "").strip()
    kernel = await _kernel()
    if kind == "episode":
        raise HTTPException(status_code=400, detail="Episode 由段落自动生成，不能单独删除")
    if kind == "fact":
        result = await kernel.memory_fact_admin(action="retract", claim_id=token, reason="console_delete")
        if not result.get("success"):
            raise HTTPException(status_code=404, detail=str(result.get("error") or "事实不存在"))
        return {"deleted": True, "result": result}
    mode = {"paragraph": "paragraph", "entity": "entity", "relation": "relation"}.get(kind)
    if mode is None:
        raise HTTPException(status_code=400, detail=f"不支持的记忆类型: {record_type}")
    result = await kernel.memory_delete_admin(
        action="execute",
        mode=mode,
        selector={"hashes": [token]},
        requested_by="fxbot_console",
        reason="console_delete",
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=str(result.get("error") or "删除失败"))
    return {"deleted": True, "result": result}


@router.get("/sources")
async def list_sources(tenant: str) -> dict[str, Any]:
    """当前租户的记忆来源。"""
    tenant = _tenant(tenant)
    kernel = await _kernel()
    store = _metadata(kernel)
    clause = _tenant_clause("p")
    rows = _query(
        store,
        "SELECT p.source, COUNT(*) AS count, MAX(p.created_at) AS last_updated FROM paragraphs p "
        f"WHERE COALESCE(p.is_deleted,0)=0 AND {clause} AND p.source IS NOT NULL AND p.source != '' "
        "GROUP BY p.source ORDER BY last_updated DESC",
        tuple(_tenant_params(tenant)),
    )
    names = _person_names(await _persons(tenant))
    return {
        "items": [
            {
                "source": str(row.get("source") or ""),
                "source_label": _source_label(str(row.get("source") or ""), tenant, names),
                "count": int(row.get("count") or 0),
                "last_updated": _iso(row.get("last_updated")),
            }
            for row in rows
        ]
    }


@router.get("/episodes")
async def list_episodes(tenant: str, query: str = "", limit: int = 50) -> dict[str, Any]:
    """当前租户的 Episode 列表。"""
    tenant = _tenant(tenant)
    size = max(1, min(200, int(limit)))
    keyword = str(query or "").strip()
    pattern = _like(keyword)
    kernel = await _kernel()
    store = _metadata(kernel)
    clause = _tenant_clause("p")
    rows = _query(
        store,
        "SELECT e.episode_id, e.source, e.title, e.summary, e.paragraph_count, e.event_time_start, "
        "e.event_time_end, e.participants_json, e.keywords_json, e.created_at, e.updated_at FROM episodes e "
        "WHERE EXISTS (SELECT 1 FROM episode_paragraphs ep JOIN paragraphs p ON p.hash=ep.paragraph_hash "
        f"WHERE ep.episode_id=e.episode_id AND COALESCE(p.is_deleted,0)=0 AND {clause}) "
        "AND (? = '' OR LOWER(COALESCE(e.title,'')) LIKE ? ESCAPE '\\' "
        "OR LOWER(COALESCE(e.summary,'')) LIKE ? ESCAPE '\\' "
        "OR LOWER(COALESCE(e.keywords_json,'')) LIKE ? ESCAPE '\\' "
        "OR LOWER(COALESCE(e.participants_json,'')) LIKE ? ESCAPE '\\') "
        "ORDER BY COALESCE(e.event_time_end, e.created_at, 0) DESC LIMIT ?",
        (*_tenant_params(tenant), keyword, pattern, pattern, pattern, pattern, size),
    )
    names = _person_names(await _persons(tenant))
    return {"items": [_episode_payload(row, tenant, names) for row in rows]}


@router.get("/profiles")
async def list_profiles(tenant: str, query: str = "", limit: int = 50) -> dict[str, Any]:
    """当前租户的人物画像。"""
    tenant = _tenant(tenant)
    persons = await _persons(tenant)
    if not persons:
        return {"items": []}
    kernel = await _kernel()
    store = _metadata(kernel)
    person_ids = [item["person_id"] for item in persons]
    placeholders = ",".join("?" for _ in person_ids)
    snapshots = _query(
        store,
        "SELECT s.person_id, s.profile_version, s.profile_text, s.updated_at, s.expires_at, s.source_note "
        "FROM person_profile_snapshots s JOIN (SELECT person_id, MAX(profile_version) AS max_version "
        "FROM person_profile_snapshots GROUP BY person_id) latest "
        "ON latest.person_id=s.person_id AND latest.max_version=s.profile_version "
        f"WHERE s.person_id IN ({placeholders}) ORDER BY s.updated_at DESC",
        tuple(person_ids),
    )
    fact_counts = {
        str(row.get("scope_id") or ""): int(row.get("count") or 0)
        for row in _query(
            store,
            f"SELECT scope_id, COUNT(*) AS count FROM fact_claims WHERE scope_type='person' "
            f"AND LOWER(COALESCE(status,'active'))='active' AND scope_id IN ({placeholders}) GROUP BY scope_id",
            tuple(person_ids),
        )
    }
    by_id = {item["person_id"]: item for item in persons}
    keyword = str(query or "").strip().lower()
    items = []
    for row in snapshots:
        person_id = str(row.get("person_id") or "")
        person = by_id.get(person_id, {})
        text = str(row.get("profile_text") or "")
        name = str(person.get("person_name") or person.get("user_nickname") or person.get("user_id") or person_id)
        if keyword and keyword not in name.lower() and keyword not in person_id.lower() and keyword not in text.lower():
            continue
        items.append(
            {
                "person_id": person_id,
                "name": name,
                "user_id": str(person.get("user_id") or ""),
                "platform": str(person.get("platform") or ""),
                "profile_text": text,
                "profile_version": int(row.get("profile_version") or 0),
                "updated_at": _iso(row.get("updated_at")),
                "expires_at": _iso(row.get("expires_at")),
                "source_note": str(row.get("source_note") or ""),
                "fact_count": fact_counts.get(person_id, 0),
            }
        )
        if len(items) >= max(1, min(200, int(limit))):
            break
    return {"items": items}


@router.get("/profiles/{person_id}")
async def profile_detail(person_id: str, tenant: str) -> dict[str, Any]:
    """人物画像详情：最新快照、别名与事实。"""
    tenant = _tenant(tenant)
    persons = await _persons(tenant)
    by_id = {item["person_id"]: item for item in persons}
    person = by_id.get(str(person_id or "").strip())
    if person is None:
        raise HTTPException(status_code=404, detail="人物不属于当前租户")
    kernel = await _kernel()
    store = _metadata(kernel)
    snapshots = _query(
        store,
        "SELECT person_id, profile_version, profile_text, aliases_json, evidence_ids_json, fact_claim_ids_json, "
        "updated_at, expires_at, source_note FROM person_profile_snapshots WHERE person_id = ? "
        "ORDER BY profile_version DESC LIMIT 1",
        (person["person_id"],),
    )
    override = _query(
        store,
        "SELECT aliases_json, updated_at, updated_by, source FROM person_profile_alias_overrides "
        "WHERE person_id = ? LIMIT 1",
        (person["person_id"],),
    )
    facts = _query(
        store,
        "SELECT * FROM fact_claims WHERE scope_type='person' AND scope_id = ? "
        "ORDER BY COALESCE(updated_at, last_confirmed_at, created_at, 0) DESC LIMIT 100",
        (person["person_id"],),
    )
    snapshot = snapshots[0] if snapshots else {}
    aliases = _json(snapshot.get("aliases_json"), [])
    if override:
        aliases = _json(override[0].get("aliases_json"), aliases)
    names = _person_names(persons)
    return {
        "person": person,
        "profile": {
            "profile_version": int(snapshot.get("profile_version") or 0),
            "profile_text": str(snapshot.get("profile_text") or ""),
            "updated_at": _iso(snapshot.get("updated_at")),
            "expires_at": _iso(snapshot.get("expires_at")),
            "source_note": str(snapshot.get("source_note") or ""),
            "aliases": aliases if isinstance(aliases, list) else [],
            "alias_override": bool(override),
        },
        "facts": [_fact_payload(row, tenant, names) for row in facts],
    }


@router.get("/graph")
async def get_graph(tenant: str, limit: int = 200) -> dict[str, Any]:
    """当前租户的实体关系图。"""
    tenant = _tenant(tenant)
    size = max(1, min(500, int(limit)))
    kernel = await _kernel()
    store = _metadata(kernel)
    clause = _tenant_clause("p")
    tenant_params = tuple(_tenant_params(tenant))
    entities = _query(
        store,
        "SELECT e.hash, e.name, e.appearance_count FROM entities e WHERE COALESCE(e.is_deleted,0)=0 AND EXISTS ("
        "SELECT 1 FROM paragraph_entities pe JOIN paragraphs p ON p.hash=pe.paragraph_hash "
        f"WHERE pe.entity_hash=e.hash AND COALESCE(p.is_deleted,0)=0 AND {clause}) "
        "ORDER BY e.appearance_count DESC LIMIT ?",
        (*tenant_params, size),
    )
    relations = _query(
        store,
        "SELECT r.hash, r.subject, r.predicate, r.object, r.confidence, r.last_reinforced FROM relations r "
        "WHERE COALESCE(r.is_inactive,0)=0 AND EXISTS ("
        "SELECT 1 FROM paragraph_relations pr JOIN paragraphs p ON p.hash=pr.paragraph_hash "
        f"WHERE pr.relation_hash=r.hash AND COALESCE(p.is_deleted,0)=0 AND {clause}) "
        "ORDER BY COALESCE(r.last_reinforced, r.created_at, 0) DESC LIMIT ?",
        (*tenant_params, size * 4),
    )
    nodes: dict[str, dict[str, Any]] = {}
    for row in entities:
        name = str(row.get("name") or "").strip()
        if name:
            nodes[name] = {
                "id": name,
                "name": name,
                "hash": str(row.get("hash") or ""),
                "appearance_count": int(row.get("appearance_count") or 0),
            }
    edges: dict[tuple[str, str], dict[str, Any]] = {}
    for row in relations:
        subject = str(row.get("subject") or "").strip()
        obj = str(row.get("object") or "").strip()
        if not subject or not obj:
            continue
        nodes.setdefault(subject, {"id": subject, "name": subject, "hash": "", "appearance_count": 0})
        nodes.setdefault(obj, {"id": obj, "name": obj, "hash": "", "appearance_count": 0})
        key = (subject, obj)
        edge = edges.setdefault(
            key,
            {"source": subject, "target": obj, "predicates": [], "relation_hashes": [], "confidence": 0.0},
        )
        predicate = str(row.get("predicate") or "").strip()
        if predicate and predicate not in edge["predicates"]:
            edge["predicates"].append(predicate)
        edge["relation_hashes"].append(str(row.get("hash") or ""))
        edge["confidence"] = max(edge["confidence"], float(row.get("confidence") or 0.0))
    for edge in edges.values():
        edge["label"] = " / ".join(edge["predicates"])
        edge["relation_count"] = len(edge["relation_hashes"])
    return {
        "nodes": list(nodes.values()),
        "edges": list(edges.values()),
        "total_nodes": len(nodes),
        "total_edges": len(edges),
    }


@router.get("/graph/node")
async def graph_node(tenant: str, node: str) -> dict[str, Any]:
    """实体节点详情：相关关系与证据段落。"""
    tenant = _tenant(tenant)
    name = str(node or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="node 不能为空")
    kernel = await _kernel()
    store = _metadata(kernel)
    clause = _tenant_clause("p")
    tenant_params = tuple(_tenant_params(tenant))
    entities = _query(
        store,
        "SELECT e.hash, e.name, e.appearance_count, e.metadata, e.created_at FROM entities e "
        "WHERE e.name = ? OR e.hash = ? LIMIT 1",
        (name, name),
    )
    relations = _query(
        store,
        "SELECT r.hash, r.subject, r.predicate, r.object, r.confidence, r.is_pinned FROM relations r "
        "WHERE (r.subject = ? OR r.object = ?) AND EXISTS ("
        "SELECT 1 FROM paragraph_relations pr JOIN paragraphs p ON p.hash=pr.paragraph_hash "
        f"WHERE pr.relation_hash=r.hash AND COALESCE(p.is_deleted,0)=0 AND {clause}) "
        "ORDER BY COALESCE(r.last_reinforced, r.created_at, 0) DESC LIMIT 50",
        (name, name, *tenant_params),
    )
    paragraphs = _query(
        store,
        "SELECT DISTINCT p.hash, p.content, p.source, p.created_at FROM paragraphs p "
        "JOIN paragraph_entities pe ON pe.paragraph_hash=p.hash "
        "JOIN entities e ON e.hash=pe.entity_hash "
        f"WHERE (e.name = ? OR e.hash = ?) AND COALESCE(p.is_deleted,0)=0 AND {clause} "
        "ORDER BY COALESCE(p.created_at, 0) DESC LIMIT 20",
        (name, name, *tenant_params),
    )
    return {
        "node": entities[0] if entities else {"name": name},
        "relations": relations,
        "paragraphs": [
            {
                "hash": str(row.get("hash") or ""),
                "content": str(row.get("content") or ""),
                "source": str(row.get("source") or ""),
                "created_at": _iso(row.get("created_at")),
            }
            for row in paragraphs
        ],
    }


@router.get("/timeline")
async def timeline(tenant: str, limit: int = 100) -> dict[str, Any]:
    """当前租户的记忆变更时间线，按时间倒序合并各来源。"""
    tenant = _tenant(tenant)
    size = _limit(limit, 100, 300)
    kernel = await _kernel()
    store = _metadata(kernel)
    persons = await _persons(tenant)
    names = _person_names(persons)
    person_ids = [item["person_id"] for item in persons]
    clause = _tenant_clause("p")
    tenant_params = tuple(_tenant_params(tenant))
    per_source = min(150, max(30, size))
    events: list[dict[str, Any]] = []

    def add(
        category: str,
        event_type: str,
        title: str,
        summary: str,
        timestamp: Any,
        target_type: str,
        target_id: str,
    ) -> None:
        stamp = _float(timestamp)
        if stamp is None or stamp <= 0:
            return
        events.append(
            {
                "_ts": stamp,
                "id": f"{event_type}:{target_id}:{int(stamp)}",
                "category": category,
                "event_type": event_type,
                "title": _label(title, 120),
                "summary": _label(summary, 200),
                "timestamp": _iso(stamp),
                "target": {"type": target_type, "id": target_id},
            }
        )

    for row in _query(
        store,
        "SELECT p.hash, p.content, p.source, p.created_at, p.updated_at, p.is_deleted FROM paragraphs p "
        f"WHERE {clause} ORDER BY COALESCE(p.updated_at, p.created_at, 0) DESC LIMIT ?",
        (*tenant_params, per_source),
    ):
        source = _source_label(str(row.get("source") or ""), tenant, names)
        add(
            "paragraph",
            "paragraph_deleted" if int(row.get("is_deleted") or 0) else "paragraph_created",
            _label(row.get("content"), 120) or "未命名段落",
            source,
            row.get("created_at") or row.get("updated_at"),
            "paragraph",
            str(row.get("hash") or ""),
        )
        created = _float(row.get("created_at"))
        updated = _float(row.get("updated_at"))
        if created and updated and updated > created + 1:
            add(
                "paragraph",
                "paragraph_updated",
                _label(row.get("content"), 120) or "未命名段落",
                f"{source} · 内容更新",
                updated,
                "paragraph",
                str(row.get("hash") or ""),
            )

    for row in _query(
        store,
        "SELECT e.episode_id, e.title, e.summary, e.source, e.created_at, e.updated_at FROM episodes e "
        "WHERE EXISTS (SELECT 1 FROM episode_paragraphs ep JOIN paragraphs p ON p.hash=ep.paragraph_hash "
        f"WHERE ep.episode_id=e.episode_id AND {clause}) "
        "ORDER BY COALESCE(e.updated_at, e.created_at, 0) DESC LIMIT ?",
        (*tenant_params, per_source),
    ):
        add(
            "episode",
            "episode_created",
            str(row.get("title") or "未命名情景"),
            _label(row.get("summary"), 180),
            row.get("created_at") or row.get("updated_at"),
            "episode",
            str(row.get("episode_id") or ""),
        )

    scope_sql, scope_params = _scope_sql(tenant, person_ids)
    for row in _query(
        store,
        "SELECT f.claim_id, f.fact_key, f.value_text, f.status, f.created_at, f.updated_at FROM fact_claims f "
        f"WHERE {scope_sql} ORDER BY f.updated_at DESC LIMIT ?",
        (*scope_params, per_source),
    ):
        title = " ".join(
            item for item in (str(row.get("fact_key") or "").strip(), str(row.get("value_text") or "").strip()) if item
        )
        status = str(row.get("status") or "").strip().lower()
        add(
            "fact",
            "fact_retracted" if status == "retracted" else "fact_created",
            title or "人物事实",
            status,
            row.get("created_at") or row.get("updated_at"),
            "fact",
            str(row.get("claim_id") or ""),
        )

    hashes = _tenant_hashes(store, tenant)
    operations = await kernel.memory_delete_admin(action="list_operations", limit=min(60, per_source))
    for summary in operations.get("items") or []:
        operation_id = str(summary.get("operation_id") or "")
        detail = await kernel.memory_delete_admin(action="get_operation", operation_id=operation_id)
        operation = detail.get("operation") if isinstance(detail.get("operation"), dict) else {}
        matched = _operation_items(operation, hashes)
        if not matched:
            continue
        add(
            "delete",
            "memory_deleted",
            f"删除 {len(matched)} 条{_mode_label(operation.get('mode'))}",
            str(operation.get("reason") or ""),
            operation.get("created_at"),
            "delete_operation",
            operation_id,
        )
        if operation.get("restored_at"):
            add(
                "delete",
                "memory_restored",
                "恢复删除操作",
                str(operation.get("reason") or ""),
                operation.get("restored_at"),
                "delete_operation",
                operation_id,
            )

    feedback = await kernel.memory_feedback_admin(action="list", limit=per_source)
    for task in feedback.get("items") or []:
        if str(task.get("session_id") or "") != tenant:
            continue
        add(
            "feedback",
            "feedback_correction",
            str(task.get("query_text") or "反馈纠错"),
            f"{task.get('task_status') or ''} · {task.get('decision') or ''}".strip(" ·"),
            task.get("created_at") or task.get("updated_at"),
            "feedback",
            str(task.get("task_id") or ""),
        )

    corrections = await kernel.memory_correction_admin(action="list", limit=per_source)
    for plan in corrections.get("items") or []:
        if (
            str(plan.get("target_chat_id") or "") != tenant
            and str(plan.get("target_person_id") or "") not in person_ids
        ):
            continue
        add(
            "correction",
            "memory_correction",
            str(plan.get("request_text") or "记忆修正"),
            f"{plan.get('scope') or ''} · {plan.get('status') or ''}".strip(" ·"),
            plan.get("created_at") or plan.get("updated_at"),
            "correction",
            str(plan.get("plan_id") or ""),
        )

    events.sort(key=lambda item: item["_ts"], reverse=True)
    return {"items": [{key: value for key, value in item.items() if key != "_ts"} for item in events[:size]]}


@router.get("/recycle")
async def recycle_bin(tenant: str, limit: int = 50) -> dict[str, Any]:
    """当前租户可恢复的删除操作与已撤回事实。"""
    tenant = _tenant(tenant)
    size = _limit(limit, 50, 200)
    kernel = await _kernel()
    store = _metadata(kernel)
    hashes = _tenant_hashes(store, tenant)
    persons = await _persons(tenant)
    scope_sql, scope_params = _scope_sql(tenant, [item["person_id"] for item in persons])
    items: list[dict[str, Any]] = []

    operations = await kernel.memory_delete_admin(action="list_operations", limit=max(size * 4, 100))
    for summary in operations.get("items") or []:
        if len(items) >= size:
            break
        operation_id = str(summary.get("operation_id") or "")
        detail = await kernel.memory_delete_admin(action="get_operation", operation_id=operation_id)
        operation = detail.get("operation") if isinstance(detail.get("operation"), dict) else {}
        matched = _operation_items(operation, hashes)
        if not matched:
            continue
        items.append(
            {
                "id": operation_id,
                "kind": "operation",
                "mode": str(operation.get("mode") or ""),
                "title": f"{_mode_label(operation.get('mode'))}删除",
                "summary": str(operation.get("reason") or ""),
                "count": len(matched),
                "status": str(operation.get("status") or ""),
                "created_at": _iso(operation.get("created_at")),
                "restored_at": _iso(operation.get("restored_at")),
                "preview": [_item_preview(item) for item in matched[:8]],
            }
        )

    for row in _query(
        store,
        "SELECT f.claim_id, f.fact_key, f.value_text, f.updated_at FROM fact_claims f "
        f"WHERE LOWER(COALESCE(f.status,'active'))='retracted' AND {scope_sql} "
        "ORDER BY f.updated_at DESC LIMIT ?",
        (*scope_params, size),
    ):
        items.append(
            {
                "id": str(row.get("claim_id") or ""),
                "kind": "fact",
                "mode": "fact",
                "title": _label(
                    " ".join(
                        item
                        for item in (str(row.get("fact_key") or "").strip(), str(row.get("value_text") or "").strip())
                        if item
                    ),
                    120,
                )
                or "人物事实",
                "summary": "已撤回",
                "count": 1,
                "status": "retracted",
                "created_at": _iso(row.get("updated_at")),
                "restored_at": None,
                "preview": [],
            }
        )

    items.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return {"items": items[:size]}


@router.post("/recycle/restore")
async def restore_deleted(payload: dict[str, Any]) -> dict[str, Any]:
    """恢复删除操作或已撤回事实。"""
    tenant = _tenant(str(payload.get("tenant") or ""))
    kind = str(payload.get("kind") or "").strip().lower()
    token = str(payload.get("id") or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="id 不能为空")
    kernel = await _kernel()
    if kind == "fact":
        store = _metadata(kernel)
        persons = await _persons(tenant)
        scope_sql, scope_params = _scope_sql(tenant, [item["person_id"] for item in persons])
        owned = _query(
            store,
            f"SELECT f.claim_id FROM fact_claims f WHERE f.claim_id = ? AND {scope_sql}",
            (token, *scope_params),
        )
        if not owned:
            raise HTTPException(status_code=404, detail="事实不属于当前会话")
        result = await kernel.memory_fact_admin(action="restore", claim_id=token, reason="console_restore")
    else:
        store = _metadata(kernel)
        detail = await kernel.memory_delete_admin(action="get_operation", operation_id=token)
        operation = detail.get("operation") if isinstance(detail.get("operation"), dict) else {}
        if not _operation_items(operation, _tenant_hashes(store, tenant)):
            raise HTTPException(status_code=404, detail="删除记录不属于当前会话")
        result = await kernel.memory_delete_admin(
            action="restore",
            operation_id=token,
            requested_by="fxbot_console",
            reason="console_restore",
        )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=str(result.get("error") or result.get("detail") or "恢复失败"))
    return {"success": True, "result": result}


@router.post("/recycle/purge")
async def purge_deleted(payload: dict[str, Any]) -> dict[str, Any]:
    """清理超过保留期的已删除记忆。"""
    _tenant(str(payload.get("tenant") or ""))
    try:
        grace_hours = float(payload.get("grace_hours", 24) or 24)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="grace_hours 必须是数字") from exc
    kernel = await _kernel()
    result = await kernel.memory_delete_admin(action="purge", grace_hours=grace_hours, limit=1000)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=str(result.get("error") or "清理失败"))
    return {"success": True, "result": result}


@router.get("/corrections")
async def corrections(tenant: str, kind: str = "", limit: int = 50) -> dict[str, Any]:
    """反馈纠错任务与模糊修正计划。"""
    tenant = _tenant(tenant)
    size = _limit(limit, 50, 200)
    kernel = await _kernel()
    persons = await _persons(tenant)
    person_ids = [item["person_id"] for item in persons]
    selected = str(kind or "").strip().lower()
    items: list[dict[str, Any]] = []

    if selected in {"", "feedback"}:
        feedback = await kernel.memory_feedback_admin(action="list", limit=max(size * 2, 100))
        for task in feedback.get("items") or []:
            if str(task.get("session_id") or "") != tenant:
                continue
            items.append(
                {
                    "id": str(task.get("task_id") or ""),
                    "kind": "feedback",
                    "title": _label(task.get("query_text"), 120) or "反馈纠错",
                    "status": str(task.get("task_status") or ""),
                    "decision": str(task.get("decision") or ""),
                    "rollback_status": str(task.get("rollback_status") or "none"),
                    "created_at": _iso(task.get("created_at")),
                    "updated_at": _iso(task.get("updated_at")),
                    "detail": task,
                }
            )

    if selected in {"", "plan"}:
        hashes = _tenant_hashes(_metadata(kernel), tenant)
        plans = await kernel.memory_correction_admin(action="list", limit=max(size * 2, 100))
        for plan in plans.get("items") or []:
            owns_plan = _tenant_owns_plan(plan, tenant, hashes)
            if not owns_plan and str(plan.get("target_person_id") or "") not in person_ids:
                continue
            items.append(
                {
                    "id": str(plan.get("plan_id") or ""),
                    "kind": "plan",
                    "title": _label(plan.get("request_text"), 120) or "记忆修正",
                    "status": str(plan.get("status") or ""),
                    "decision": str(plan.get("scope") or ""),
                    "rollback_status": "-",
                    "created_at": _iso(plan.get("created_at")),
                    "updated_at": _iso(plan.get("updated_at")),
                    "detail": plan,
                }
            )

    items.sort(key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""), reverse=True)
    return {"items": items[:size]}


def _correction_targets(value: Any) -> list[dict[str, str]]:
    """校验勾选的记忆目标；批量修正只支持段落与关系。"""
    if not isinstance(value, list) or not value:
        raise HTTPException(status_code=400, detail="请至少选择一条记忆")
    targets: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            raise HTTPException(status_code=400, detail="所选记忆格式无效")
        kind = str(item.get("type") or "").strip().lower()
        token = str(item.get("id") or "").strip()
        if kind not in {"paragraph", "relation"} or not token:
            raise HTTPException(status_code=400, detail="只能修正段落或关系")
        if {"type": kind, "id": token} not in targets:
            targets.append({"type": kind, "id": token})
    return targets


@router.post("/corrections/preview")
async def preview_correction(payload: dict[str, Any]) -> dict[str, Any]:
    """按勾选的记忆生成修改计划，等待确认后执行。"""
    tenant = _tenant(str(payload.get("tenant") or ""))
    request_text = str(payload.get("request_text") or "").strip()
    if not request_text:
        raise HTTPException(status_code=400, detail="请填写修改内容")
    targets = _correction_targets(payload.get("targets"))
    kernel = await _kernel()
    paragraphs, _, relations = _tenant_hashes(_metadata(kernel), tenant)
    for target in targets:
        owned = paragraphs if target["type"] == "paragraph" else relations
        if target["id"] not in owned:
            raise HTTPException(status_code=404, detail="所选记忆不属于当前会话")
    result = await kernel.memory_correction_admin(
        action="preview",
        request_text=request_text,
        scope="memory",
        targets=targets,
        requested_by="fxbot_console",
        reason="console_batch_correction",
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=str(result.get("error") or "生成修正计划失败"))
    return result


@router.post("/corrections/execute")
async def execute_correction(payload: dict[str, Any]) -> dict[str, Any]:
    """确认并执行修正计划。"""
    tenant = _tenant(str(payload.get("tenant") or ""))
    plan_id = str(payload.get("plan_id") or "").strip()
    if not plan_id:
        raise HTTPException(status_code=400, detail="plan_id 不能为空")
    kernel = await _kernel()
    detail = await kernel.memory_correction_admin(action="get", plan_id=plan_id)
    plan = detail.get("plan") if isinstance(detail.get("plan"), dict) else {}
    if not plan or not _tenant_owns_plan(plan, tenant, _tenant_hashes(_metadata(kernel), tenant)):
        raise HTTPException(status_code=404, detail="修正计划不属于当前会话")
    result = await kernel.memory_correction_admin(
        action="execute",
        plan_id=plan_id,
        confirmed=True,
        requested_by="fxbot_console",
        reason="console_batch_correction",
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=str(result.get("error") or "执行修正失败"))
    return result


@router.post("/corrections/rollback")
async def rollback_correction(payload: dict[str, Any]) -> dict[str, Any]:
    """回滚一次反馈纠错或模糊修正。"""
    tenant = _tenant(str(payload.get("tenant") or ""))
    kind = str(payload.get("kind") or "").strip().lower()
    token = str(payload.get("id") or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="id 不能为空")
    kernel = await _kernel()
    if kind == "feedback":
        try:
            task_id = int(token)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="反馈任务 id 无效") from exc
        detail = await kernel.memory_feedback_admin(action="get", task_id=task_id)
        task = detail.get("task") if isinstance(detail.get("task"), dict) else {}
        if str(task.get("session_id") or "") != tenant:
            raise HTTPException(status_code=404, detail="纠正记录不属于当前会话")
        result = await kernel.memory_feedback_admin(
            action="rollback",
            task_id=task_id,
            requested_by="fxbot_console",
            reason=str(payload.get("reason") or "console_rollback"),
        )
    elif kind == "plan":
        detail = await kernel.memory_correction_admin(action="get", plan_id=token)
        plan = detail.get("plan") if isinstance(detail.get("plan"), dict) else {}
        persons = await _persons(tenant)
        if str(plan.get("target_chat_id") or "") != tenant and str(plan.get("target_person_id") or "") not in {
            item["person_id"] for item in persons
        }:
            raise HTTPException(status_code=404, detail="纠正记录不属于当前会话")
        result = await kernel.memory_correction_admin(
            action="rollback",
            plan_id=token,
            requested_by="fxbot_console",
            reason=str(payload.get("reason") or "console_rollback"),
        )
    else:
        raise HTTPException(status_code=400, detail="kind 必须是 feedback 或 plan")
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=str(result.get("error") or "回滚失败"))
    return {"success": True, "result": result}


@router.get("/bundles")
async def list_bundles(limit: int = 50) -> dict[str, Any]:
    """已安装记忆包。"""
    kernel = await _kernel()
    result = await kernel.memory_bundle_admin(action="list", limit=_limit(limit, 50, 200))
    for item in result.get("items") or []:
        item["installed_at"] = _iso(item.get("installed_at"))
        item["updated_at"] = _iso(item.get("updated_at"))
    return result


@router.get("/bundles/inspect")
async def inspect_bundle(path: str) -> dict[str, Any]:
    """检查记忆包文件。"""
    kernel = await _kernel()
    try:
        result = await kernel.memory_bundle_admin(action="inspect", path=path)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result


@router.post("/bundles/export")
async def export_bundle(payload: dict[str, Any]) -> dict[str, Any]:
    """导出当前会话或全库记忆包。"""
    tenant = str(payload.get("tenant") or "").strip()
    selector = {"type": "chat", "chat_id": _tenant(tenant)} if tenant else {"type": "all"}
    kernel = await _kernel()
    try:
        result = await kernel.memory_bundle_admin(
            action="export",
            selector=selector,
            content_level=str(payload.get("content_level") or "knowledge"),
            include_images=True,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=str(result.get("error") or "导出失败"))
    return result


@router.post("/bundles/import")
async def import_bundle(payload: dict[str, Any]) -> dict[str, Any]:
    """上传并安装记忆包。"""
    kernel = await _kernel()
    file_name = _safe_file_name(payload.get("file_name") or "memory", ".amembundle")
    content = _decode_base64(payload.get("content_base64"), field="content_base64")
    target = _kernel_path(kernel, "imports", "bundles") / file_name
    target.write_bytes(content)
    tenant = str(payload.get("tenant") or "").strip()
    try:
        result = await kernel.memory_bundle_admin(
            action="import",
            path=str(target),
            mode=str(payload.get("mode") or "merge"),
            scope_type="chat" if tenant else "global",
            chat_id=_tenant(tenant) if tenant else "",
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=str(result.get("error") or "安装失败"))
    return result


@router.post("/bundles/uninstall")
async def uninstall_bundle(payload: dict[str, Any]) -> dict[str, Any]:
    """卸载记忆包。"""
    installation_id = str(payload.get("installation_id") or "").strip()
    if not installation_id:
        raise HTTPException(status_code=400, detail="installation_id 不能为空")
    kernel = await _kernel()
    try:
        result = await kernel.memory_bundle_admin(action="uninstall", installation_id=installation_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=str(result.get("error") or "卸载失败"))
    return result


@file_router.get("/bundles/file")
async def download_bundle(file_name: str) -> FileResponse:
    """下载已导出的记忆包。"""
    kernel = await _kernel()
    try:
        result = await kernel.memory_bundle_admin(action="resolve_file", file_name=file_name)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    path = Path(str(result.get("path") or ""))
    if not result.get("success") or not path.is_file():
        raise HTTPException(status_code=404, detail="记忆包不存在")
    return FileResponse(path, media_type="application/zip", filename=path.name)


@router.get("/imports")
async def list_imports(limit: int = 50) -> dict[str, Any]:
    """导入任务列表与运行时限制。"""
    kernel = await _kernel()
    result = await kernel.memory_import_admin(action="list", limit=_limit(limit, 50, 200))
    settings = await kernel.memory_import_admin(action="get_settings")
    return {**result, "settings": settings.get("settings") or {}}


@router.get("/imports/settings")
async def import_settings() -> dict[str, Any]:
    """导入运行时限制。"""
    kernel = await _kernel()
    return await kernel.memory_import_admin(action="get_settings")


@router.get("/imports/{task_id}")
async def import_task(task_id: str, chunks: bool = False) -> dict[str, Any]:
    """导入任务详情。"""
    kernel = await _kernel()
    return await kernel.memory_import_admin(action="get", task_id=task_id, include_chunks=chunks)


@router.post("/imports/paste")
async def paste_import(payload: dict[str, Any]) -> dict[str, Any]:
    """粘贴文本导入。"""
    tenant = _tenant(str(payload.get("tenant") or ""))
    content = str(payload.get("content") or "")
    if not content.strip():
        raise HTTPException(status_code=400, detail="content 不能为空")
    kernel = await _kernel()
    try:
        result = await kernel.memory_import_admin(
            action="create_paste",
            name=str(payload.get("name") or "paste.txt"),
            content=content,
            chat_id=tenant,
            scope_type="chat",
            input_mode=str(payload.get("input_mode") or "text"),
            llm_enabled=bool(payload.get("llm_enabled", True)),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result


@router.post("/imports/upload")
async def upload_import(payload: dict[str, Any]) -> dict[str, Any]:
    """上传 txt/md/json 文本导入。"""
    tenant = _tenant(str(payload.get("tenant") or ""))
    raw_name = str(payload.get("file_name") or "upload.txt")
    file_name = _safe_file_name(raw_name, Path(raw_name).suffix.lower())
    if Path(file_name).suffix.lower() not in {".txt", ".md", ".json"}:
        raise HTTPException(status_code=400, detail="只支持 txt、md、json")
    content = _decode_base64(payload.get("content_base64"), field="content_base64")
    kernel = await _kernel()
    staging = _kernel_path(kernel, "imports", "staging")
    path = staging / f"{uuid.uuid4().hex}_{file_name}"
    path.write_bytes(content)
    try:
        result = await kernel.memory_import_admin(
            action="create_upload",
            staged_files=[{"path": str(path), "name": file_name}],
            chat_id=tenant,
            scope_type="chat",
            input_mode=str(payload.get("input_mode") or "text"),
            llm_enabled=bool(payload.get("llm_enabled", True)),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        path.unlink(missing_ok=True)
    return result


@router.post("/imports/{task_id}/cancel")
async def cancel_import(task_id: str) -> dict[str, Any]:
    """取消导入任务。"""
    kernel = await _kernel()
    return await kernel.memory_import_admin(action="cancel", task_id=task_id)


@router.post("/imports/{task_id}/retry")
async def retry_import(task_id: str) -> dict[str, Any]:
    """重试导入任务中失败的片段。"""
    kernel = await _kernel()
    return await kernel.memory_import_admin(action="retry_failed", task_id=task_id)


@router.get("/images")
async def list_images(tenant: str, limit: int = 50, offset: int = 0) -> dict[str, Any]:
    """当前会话的图片记忆资产。"""
    tenant = _tenant(tenant)
    kernel = await _kernel()
    try:
        result = await kernel.image_memory(
            action="list", limit=_limit(limit, 50, 200), offset=max(0, int(offset)), chat_id=tenant
        )
    except RuntimeError as exc:
        return {"success": False, "error": str(exc), "items": []}
    runtime = getattr(kernel, "image_memory_runtime", None)
    store = getattr(runtime, "metadata_store", None)
    if store is not None:
        for item in result.get("items") or []:
            occurrences = store.list_image_occurrences_for_asset(str(item.get("asset_id") or ""))
            mine = [
                row
                for row in occurrences
                if str(row.get("chat_id") or "") == tenant and str(row.get("status") or "") == "active"
            ]
            item["occurrence_id"] = str(mine[0].get("occurrence_id") or "") if mine else ""
    return result


@router.get("/images/status")
async def image_status() -> dict[str, Any]:
    """图片记忆运行时状态。"""
    kernel = await _kernel()
    try:
        return await kernel.image_memory(action="status")
    except RuntimeError as exc:
        return {"success": False, "error": str(exc)}


@router.post("/images/retry")
async def retry_images() -> dict[str, Any]:
    """重试失败的图片嵌入任务。"""
    kernel = await _kernel()
    try:
        return await kernel.image_memory(action="retry_failed_jobs")
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/images")
async def upload_image(payload: dict[str, Any]) -> dict[str, Any]:
    """手动写入一张图片记忆。"""
    tenant = _tenant(str(payload.get("tenant") or ""))
    image = _decode_base64(payload.get("content_base64"), field="content_base64")
    kernel = await _kernel()
    try:
        result = await kernel.image_memory(
            action="ingest",
            image_bytes=image,
            source_kind="manual",
            external_ref=f"manual:{tenant}:{uuid.uuid4().hex}",
            scope_type="chat",
            chat_id=tenant,
            user_statement=str(payload.get("description") or ""),
        )
    except (RuntimeError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result


@router.delete("/images/{occurrence_id}")
async def delete_image(occurrence_id: str, tenant: str) -> dict[str, Any]:
    """删除一张图片的当前出现记录。"""
    tenant = _tenant(tenant)
    kernel = await _kernel()
    runtime = getattr(kernel, "image_memory_runtime", None)
    store = getattr(runtime, "metadata_store", None)
    occurrence = store.get_image_occurrence(occurrence_id) if store is not None else None
    if occurrence is None or str(occurrence.get("chat_id") or "") != tenant:
        raise HTTPException(status_code=404, detail="图片出现记录不存在")
    try:
        result = await kernel.image_memory(action="delete_occurrence", occurrence_id=occurrence_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=str(result.get("error") or "删除失败"))
    return result


@file_router.get("/images/{occurrence_id}/file")
async def image_file(occurrence_id: str, tenant: str) -> FileResponse:
    """返回图片原图，供控制台预览；支持查询参数 token。"""
    tenant = _tenant(tenant)
    kernel = await _kernel()
    runtime = getattr(kernel, "image_memory_runtime", None)
    store = getattr(runtime, "metadata_store", None)
    occurrence = store.get_image_occurrence(occurrence_id) if store is not None else None
    if occurrence is None or str(occurrence.get("chat_id") or "") != tenant:
        raise HTTPException(status_code=404, detail="图片不存在")
    asset = store.get_image_asset(str(occurrence.get("asset_id") or ""))
    if asset is None:
        raise HTTPException(status_code=404, detail="图片资产不存在")
    path = runtime.asset_store.path_for_read(str(asset.get("storage_key") or ""))
    if not path.is_file():
        raise HTTPException(status_code=404, detail="图片文件不存在")
    return FileResponse(path, media_type=str(asset.get("mime_type") or "application/octet-stream"))


@router.get("/runtime")
async def runtime_status() -> dict[str, Any]:
    """内核运行状态与向量能力。"""
    if not amemorix_runtime.available:
        return {"available": False, "error": amemorix_runtime.error, "config": await _config_payload()}
    kernel = await _kernel()
    result = await kernel.memory_runtime_admin(action="get_config")
    result.pop("config", None)
    return {**result, "available": True}


@router.post("/runtime/self-check")
async def runtime_self_check() -> dict[str, Any]:
    """重新探测嵌入与向量通道。"""
    kernel = await _kernel()
    return await kernel.memory_runtime_admin(action="self_check")


async def _config_payload() -> dict[str, Any]:
    return amemorix_runtime.config().model_dump()


@router.get("/config")
async def get_config() -> dict[str, Any]:
    """原始 A_Memorix 配置，字段与 MaiBot 一致。"""
    return {"config": await _config_payload()}


@router.put("/config")
async def update_config(payload: dict[str, Any]) -> dict[str, Any]:
    """校验并保存配置；内核重启后生效。"""
    raw = payload.get("config") if isinstance(payload.get("config"), dict) else payload
    if not isinstance(raw, dict):
        raise HTTPException(status_code=400, detail="config 必须是对象")
    try:
        config = save_amemorix_config(raw)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"配置校验失败: {exc}") from exc
    return {"config": config.model_dump(), "restart_required": True}
