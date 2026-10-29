"""A_Memorix 对聊天侧暴露的最小接口。"""

from __future__ import annotations

from typing import Any

from nonebot import logger

from .people import session_parts
from .runtime import amemorix_runtime
from ._host.person_db import person_scope, resolve_person_identifier


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
