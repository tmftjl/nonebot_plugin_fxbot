"""A_Memorix 对聊天侧暴露的最小接口。"""

from __future__ import annotations

import asyncio
from typing import Any
from collections.abc import Iterable

from nonebot import logger

from .people import session_parts
from .runtime import amemorix_runtime
from ._host.person_db import person_scope, resolve_person_identifier

_WIPE_BATCH = 200


def _person_placeholders(person_ids: list[str]) -> str:
    return ",".join("?" for _ in person_ids)


def _tenant_paragraph_hashes(store: Any, tenant: str) -> list[str]:
    rows = store.query(
        "SELECT p.hash FROM paragraphs p WHERE COALESCE(p.is_deleted,0)=0 AND ("
        "(CASE WHEN json_valid(p.metadata) THEN json_extract(p.metadata,'$.chat_id') END) = ? "
        "OR p.source = ?)",
        (tenant, f"chat_summary:{tenant}"),
    )
    return [str(row.get("hash") or "") for row in rows if str(row.get("hash") or "")]


def _delete_tenant_records(store: Any, tenant: str, person_ids: list[str]) -> None:
    """删除事实、画像与摘要检查点；段落由 delete_admin 负责级联和向量清理。"""
    with store.transaction(immediate=True) as conn:
        conn.execute(
            "DELETE FROM fact_evidence WHERE claim_id IN "
            "(SELECT claim_id FROM fact_claims WHERE scope_type='chat' AND scope_id=?)",
            (tenant,),
        )
        conn.execute("DELETE FROM fact_claims WHERE scope_type='chat' AND scope_id=?", (tenant,))
        conn.execute("DELETE FROM memory_fuzzy_modify_plans WHERE target_chat_id=?", (tenant,))
        for start in range(0, len(person_ids), _WIPE_BATCH):
            chunk = person_ids[start : start + _WIPE_BATCH]
            marks = _person_placeholders(chunk)
            conn.execute(
                f"DELETE FROM fact_evidence WHERE claim_id IN "
                f"(SELECT claim_id FROM fact_claims WHERE scope_type='person' AND scope_id IN ({marks}))",
                chunk,
            )
            conn.execute(f"DELETE FROM fact_claims WHERE scope_type='person' AND scope_id IN ({marks})", chunk)
            for table in (
                "person_profile_snapshots",
                "person_profile_overrides",
                "person_profile_alias_overrides",
                "person_profile_refresh_queue",
                "person_profile_active_persons",
            ):
                conn.execute(f"DELETE FROM {table} WHERE person_id IN ({marks})", chunk)
            conn.execute(f"DELETE FROM memory_fuzzy_modify_plans WHERE target_person_id IN ({marks})", chunk)
        conn.execute("DELETE FROM chat_summary_checkpoints WHERE chat_id=?", (tenant,))
        conn.execute(
            "DELETE FROM memory_feedback_action_logs WHERE task_id IN "
            "(SELECT id FROM memory_feedback_tasks WHERE session_id=?)",
            (tenant,),
        )
        conn.execute("DELETE FROM memory_feedback_tasks WHERE session_id=?", (tenant,))


async def _delete_tenant_images(kernel: Any, store: Any, tenant: str) -> None:
    rows = await asyncio.to_thread(
        store.query,
        "SELECT occurrence_id FROM image_occurrences WHERE status='active' AND chat_id=?",
        (tenant,),
    )
    for row in rows:
        try:
            await kernel.image_memory(action="delete_occurrence", occurrence_id=str(row.get("occurrence_id") or ""))
        except Exception:
            logger.opt(exception=True).debug("[A_Memorix] 会话图片记忆删除失败")


async def wipe_tenant(tenant: str, person_ids: Iterable[str] = ()) -> dict[str, Any]:
    """清空该会话在 A_Memorix 中的段落、人物、事实与画像。"""
    if not amemorix_runtime.config().plugin.enabled:
        return {"success": True, "skipped": "disabled"}
    kernel = await amemorix_runtime.get_kernel()
    if not _kernel_ready(kernel) or kernel.metadata_store is None:
        return {"success": False, "error": "memory_unavailable"}
    store = kernel.metadata_store
    people = [str(item) for item in person_ids if str(item)]
    deleted = 0
    with person_scope(tenant):
        try:
            while True:
                hashes = await asyncio.to_thread(_tenant_paragraph_hashes, store, tenant)
                if not hashes:
                    break
                result = await kernel.memory_delete_admin(
                    action="execute",
                    mode="paragraph",
                    selector={"hashes": hashes[:_WIPE_BATCH]},
                    requested_by="fxbot_session_reset",
                    reason="session_reset",
                )
                if not result.get("success"):
                    return {"success": False, "error": str(result.get("error") or "删除段落失败")}
                progress = int(result.get("deleted_paragraph_count") or 0)
                if progress <= 0:
                    return {"success": False, "error": "删除段落无进展"}
                deleted += progress
            await asyncio.to_thread(_delete_tenant_records, store, tenant, people)
            if amemorix_runtime.config().image_memory.enabled:
                await _delete_tenant_images(kernel, store, tenant)
        except Exception as exc:
            logger.opt(exception=True).warning("[A_Memorix] 会话长期记忆清理失败")
            return {"success": False, "error": f"{type(exc).__name__}: {exc}"}
    return {"success": True, "paragraphs": deleted}


def _request_context(tenant: str, user_id: str = "", group_id: str = "") -> tuple[str, str]:
    platform, target = session_parts(tenant)
    del platform
    if not group_id and ":group:" in str(tenant or ""):
        group_id = target
    if not user_id and ":private:" in str(tenant or ""):
        user_id = target
    return str(user_id or ""), str(group_id or "")


def _kernel_ready(kernel: Any) -> bool:
    return bool(amemorix_runtime.available and kernel is not None)


async def search(
    tenant: str,
    query: str,
    *,
    limit: int = 5,
    mode: str = "search",
    chat_id: str | None = None,
    person_id: str = "",
    time_start: str | float | None = None,
    time_end: str | float | None = None,
    respect_filter: bool = True,
    user_id: str = "",
    group_id: str = "",
) -> dict[str, Any]:
    kernel = await amemorix_runtime.get_kernel()
    if not _kernel_ready(kernel):
        return {"success": False, "error": "memory_unavailable", "hits": []}
    user_id, group_id = _request_context(tenant, user_id, group_id)
    from .core.runtime.models import KernelSearchRequest

    with person_scope(tenant):
        try:
            result = await kernel.search_memory(
                KernelSearchRequest(
                    query=str(query or "").strip(),
                    limit=max(1, int(limit)),
                    mode=str(mode or "search"),
                    chat_id=tenant if chat_id is None else str(chat_id or ""),
                    person_id=str(person_id or ""),
                    time_start=time_start,
                    time_end=time_end,
                    respect_filter=bool(respect_filter),
                    user_id=user_id,
                    group_id=group_id,
                )
            )
            paragraph_hits = [
                hit for hit in result.get("hits", []) if hit.get("type") == "paragraph" and hit.get("hash")
            ]
            if paragraph_hits and kernel.metadata_store is not None:
                paragraphs = kernel.metadata_store.get_paragraphs_by_hashes([hit["hash"] for hit in paragraph_hits])
                for hit in paragraph_hits:
                    paragraph = paragraphs.get(hit["hash"])
                    if paragraph is not None:
                        hit["source"] = paragraph["source"]
                        hit["metadata"] = {**(paragraph.get("metadata") or {}), **(hit.get("metadata") or {})}
            return {**result, "success": not bool(result.get("error")) and bool(result.get("retrieval_ready"))}
        except Exception as exc:
            logger.opt(exception=True).warning("[A_Memorix] 记忆检索失败")
            return {"success": False, "error": f"{type(exc).__name__}: {exc}", "hits": []}


async def ingest_summary(
    tenant: str,
    *,
    external_id: str,
    text: str = "",
    participants: list[str] | None = None,
    time_end: float | None = None,
    metadata: dict[str, Any] | None = None,
    respect_filter: bool = True,
    user_id: str = "",
    group_id: str = "",
) -> dict[str, Any]:
    kernel = await amemorix_runtime.get_kernel()
    if not _kernel_ready(kernel):
        return {"success": False, "detail": "memory_unavailable"}
    user_id, group_id = _request_context(tenant, user_id, group_id)
    with person_scope(tenant):
        try:
            return await kernel.ingest_summary(
                external_id=external_id,
                chat_id=tenant,
                text=text,
                participants=participants or [],
                time_end=time_end,
                metadata=metadata,
                respect_filter=respect_filter,
                user_id=user_id,
                group_id=group_id,
            )
        except Exception as exc:
            logger.opt(exception=True).warning("[A_Memorix] 摘要写入失败")
            return {"success": False, "detail": f"{type(exc).__name__}: {exc}"}


async def ingest_text(
    tenant: str,
    *,
    external_id: str,
    source_type: str,
    text: str,
    person_ids: list[str] | None = None,
    participants: list[str] | None = None,
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    respect_filter: bool = True,
) -> dict[str, Any]:
    kernel = await amemorix_runtime.get_kernel()
    if not _kernel_ready(kernel):
        return {"success": False, "detail": "memory_unavailable"}
    user_id, group_id = _request_context(tenant)
    with person_scope(tenant):
        try:
            return await kernel.ingest_text(
                external_id=external_id,
                source_type=source_type,
                text=text,
                chat_id=tenant,
                person_ids=person_ids or [],
                participants=participants or [],
                tags=tags or [],
                metadata=metadata,
                respect_filter=respect_filter,
                user_id=user_id,
                group_id=group_id,
            )
        except Exception as exc:
            logger.opt(exception=True).warning("[A_Memorix] 文本写入失败")
            return {"success": False, "detail": f"{type(exc).__name__}: {exc}"}


async def person_profile(tenant: str, person_id: str, *, limit: int = 10) -> dict[str, Any]:
    kernel = await amemorix_runtime.get_kernel()
    if not _kernel_ready(kernel):
        return {"success": False, "error": "memory_unavailable"}
    with person_scope(tenant):
        try:
            result = await kernel.get_person_profile(person_id=person_id, chat_id=tenant, limit=max(1, int(limit)))
            return {**result, "success": not bool(result.get("error"))}
        except Exception as exc:
            logger.opt(exception=True).debug("[A_Memorix] 人物画像读取失败")
            return {"success": False, "error": f"{type(exc).__name__}: {exc}"}


def resolve_person(tenant: str, person_id: str = "", person_name: str = "") -> str:
    with person_scope(tenant):
        if person_id:
            return str(person_id or "").strip()
        return resolve_person_identifier(str(person_name or "").strip())


async def stats() -> dict[str, Any]:
    kernel = await amemorix_runtime.get_kernel()
    if not _kernel_ready(kernel):
        return {"success": False, "error": "memory_unavailable"}
    try:
        return dict(kernel.memory_stats() or {})
    except Exception as exc:
        return {"success": False, "error": f"{type(exc).__name__}: {exc}"}
