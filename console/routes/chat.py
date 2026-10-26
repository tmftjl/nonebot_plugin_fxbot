"""聊天记忆控制台：租户、群风格与黑话。"""

from __future__ import annotations

import json
from typing import Any
from datetime import datetime

from fastapi import Depends, APIRouter, HTTPException

from ..auth import bearer_auth
from ...utils.tz import as_shanghai
from ...chat.memory.repository import chat_repository

router = APIRouter(prefix="/chat", tags=["fxbot-chat"], dependencies=[Depends(bearer_auth)])


def _iso(value: datetime | None) -> str | None:
    """naive UTC 转成带 +08:00 的 ISO 时间。"""
    return as_shanghai(value).isoformat() if value else None


def _json_list(value: object) -> list[Any]:
    """解析 JSON 数组；坏数据按空处理。"""
    try:
        items = json.loads(str(value or "[]"))
    except (TypeError, ValueError):
        return []
    return items if isinstance(items, list) else []


def _tenant(value: str) -> str:
    """校验并归一化租户参数。"""
    tenant = str(value or "").strip()
    if not tenant:
        raise HTTPException(status_code=400, detail="tenant 不能为空")
    return tenant


def _style_payload(style: object) -> dict[str, Any]:
    """把群风格行转成控制台可直接展示的结构。"""
    examples = [
        item
        for item in _json_list(getattr(style, "examples", "[]"))
        if isinstance(item, dict) and str(item.get("text") or "").strip()
    ]
    return {
        "tenant": getattr(style, "tenant", ""),
        "summary": getattr(style, "summary", ""),
        "manual_override": getattr(style, "manual_override", ""),
        "rules": [str(item) for item in _json_list(getattr(style, "rules", "[]")) if str(item).strip()],
        "examples": examples,
        "version": getattr(style, "version", 0),
        "learn_count": getattr(style, "learn_count", 0),
        "use_count": getattr(style, "use_count", 0),
        "evidence_seq_start": getattr(style, "evidence_seq_start", 0),
        "evidence_seq_end": getattr(style, "evidence_seq_end", 0),
        "evidence_count": getattr(style, "evidence_count", 0),
        "last_used_at": _iso(getattr(style, "last_used_at", None)),
        "updated_at": _iso(getattr(style, "updated_at", None)),
    }


def _jargon_payload(row: object) -> dict[str, Any]:
    """把黑话行转成控制台结构。"""
    return {
        "id": getattr(row, "id", 0),
        "term": getattr(row, "term", ""),
        "meaning": getattr(row, "meaning", ""),
        "status": getattr(row, "status", ""),
        "count": getattr(row, "count", 0),
        "learn_count": getattr(row, "learn_count", 0),
        "use_count": getattr(row, "use_count", 0),
        "first_seen": _iso(getattr(row, "first_seen", None)),
        "last_seen": _iso(getattr(row, "last_seen", None)),
        "last_used_at": _iso(getattr(row, "last_used_at", None)),
    }


@router.get("/tenants")
async def list_tenants() -> dict[str, Any]:
    """列出仍有消息的租户，供控制台选择。"""
    try:
        tenants = await chat_repository.list_memory_tenants()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="聊天记忆数据库尚未就绪") from exc
    return {"tenants": tenants}


@router.get("/style")
async def get_style(tenant: str) -> dict[str, Any]:
    """读取指定租户的群风格。"""
    tenant = _tenant(tenant)
    try:
        style = await chat_repository.get_style(tenant)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="聊天记忆数据库尚未就绪") from exc
    return {"style": _style_payload(style) if style else None}


@router.delete("/style")
async def delete_style(tenant: str) -> dict[str, bool]:
    """删除群风格；不存在时明确报错。"""
    tenant = _tenant(tenant)
    try:
        deleted = await chat_repository.delete_style(tenant)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="聊天记忆数据库尚未就绪") from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="该租户没有群风格")
    return {"deleted": True}


@router.get("/jargon")
async def list_jargon(tenant: str, status: str = "all") -> dict[str, Any]:
    """列出黑话条目，可按 active/rejected 过滤。"""
    tenant = _tenant(tenant)
    if status not in {"all", "active", "rejected"}:
        raise HTTPException(status_code=400, detail="status 只能是 all/active/rejected")
    try:
        rows = await chat_repository.list_jargon(
            tenant,
            status=None if status == "all" else status,
            limit=1000,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="聊天记忆数据库尚未就绪") from exc
    return {"items": [_jargon_payload(row) for row in rows]}


@router.delete("/jargon/{jargon_id}")
async def delete_jargon(jargon_id: int, tenant: str) -> dict[str, bool]:
    """按租户删除一条黑话释义。"""
    tenant = _tenant(tenant)
    try:
        deleted = await chat_repository.delete_jargon(tenant, jargon_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="聊天记忆数据库尚未就绪") from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="黑话条目不存在")
    return {"deleted": True}
