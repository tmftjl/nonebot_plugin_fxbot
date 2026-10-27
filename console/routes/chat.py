"""聊天记忆控制台：租户、群风格与黑话。"""

from __future__ import annotations

import json
from typing import Any
from datetime import datetime

from fastapi import Depends, APIRouter, HTTPException

from ..auth import bearer_auth
from ...utils.tz import SHANGHAI_TZ, as_shanghai
from ...chat.memory.repository import BEHAVIOR_MIN_SAMPLES, chat_repository
from ...chat.behavior.reply_gate import (
    ReplyGate,
    talk_value_for,
    proactive_config,
    proactive_switch,
    default_reply_gate_registry,
)

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


def _expression_payload(row: object) -> dict[str, Any]:
    """把表达行转成控制台结构。"""
    return {
        "id": getattr(row, "id", 0),
        "situation": getattr(row, "situation", ""),
        "style": getattr(row, "style", ""),
        "type": getattr(row, "type", ""),
        "count": getattr(row, "count", 0.0),
        "checked": getattr(row, "checked", False),
        "rejected": getattr(row, "rejected", False),
        "learn_count": getattr(row, "learn_count", 0),
        "use_count": getattr(row, "use_count", 0),
        "source_message_seq": getattr(row, "source_message_seq", 0),
        "last_used_at": _iso(getattr(row, "last_used_at", None)),
        "updated_at": _iso(getattr(row, "updated_at", None)),
    }


def _planner_payload(row: object) -> dict[str, Any]:
    """把规划样本转成控制台结构。"""
    try:
        features = json.loads(str(getattr(row, "features", "{}")))
    except (TypeError, ValueError):
        features = {}
    return {
        "id": getattr(row, "id", 0),
        "turn_id": getattr(row, "turn_id", ""),
        "trigger_kind": getattr(row, "trigger_kind", ""),
        "action": getattr(row, "action", ""),
        "label": getattr(row, "label", 0),
        "features": features if isinstance(features, dict) else {},
        "provider": getattr(row, "provider", ""),
        "model": getattr(row, "model", ""),
        "created_at": _iso(getattr(row, "created_at", None)),
    }


def _behavior_payload(row: object) -> dict[str, Any]:
    """把行为聚合行转成控制台结构。"""
    return {
        "scene_tags": [str(item) for item in _json_list(getattr(row, "scene_tags", "[]"))],
        "action": getattr(row, "action", ""),
        "outcome": getattr(row, "outcome", ""),
        "confidence": getattr(row, "confidence", 0.0),
        "count": getattr(row, "count", 0),
        "last_seen": _iso(getattr(row, "last_seen", None)),
    }


def _gate_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """把内存观察转成控制台结构；墙钟时间按上海时区输出。"""
    observations: list[dict[str, Any]] = []
    for row in payload.get("observations", []):
        item = dict(row)
        occurred_at = item.get("occurred_at")
        if isinstance(occurred_at, (int, float)):
            item["occurred_at"] = datetime.fromtimestamp(occurred_at, SHANGHAI_TZ).isoformat()
        observations.append(item)
    return {
        # 发布权重只是先验，还没用本群 Planner 样本校准
        "calibrated": False,
        "window": payload.get("window", {}),
        "backoff": payload.get("backoff", {}),
        "observations": observations,
        "distribution": payload.get("distribution", []),
    }


@router.get("/tenants")
async def list_tenants() -> dict[str, Any]:
    """列出仍有消息的租户，供控制台选择。"""
    try:
        tenants = await chat_repository.list_memory_tenants()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="聊天记忆数据库尚未就绪") from exc
    return {"tenants": tenants}


@router.get("/planner")
async def list_planner_samples(tenant: str, limit: int = 100) -> dict[str, Any]:
    """列出最近的 Planner 首轮动作样本，供校准与追溯。"""
    tenant = _tenant(tenant)
    limit = max(1, min(500, int(limit)))
    try:
        rows = await chat_repository.list_planner_samples(tenant, limit)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="聊天记忆数据库尚未就绪") from exc
    return {"items": [_planner_payload(row) for row in rows]}


@router.get("/gate")
async def get_reply_gate(tenant: str, limit: int = 100) -> dict[str, Any]:
    """读取主动接话门控的观察窗口与当前开关。"""
    tenant = _tenant(tenant)
    limit = max(1, min(200, int(limit)))
    gate = default_reply_gate_registry.peek(tenant)
    payload = _gate_payload((gate or ReplyGate(tenant)).snapshot(limit))
    cfg = proactive_config()
    allowlist = {
        str(item).strip() for item in cfg.get("group_allowlist") or [] if item is not None and str(item).strip()
    }
    group_id = tenant.rsplit(":group:", 1)[1] if ":group:" in tenant else ""
    payload["proactive"] = {
        "enabled": bool(cfg.get("enabled")),
        "gate_mode": str(cfg.get("gate_mode") or "observe"),
        "talk_value": talk_value_for(tenant),
        "switch": await proactive_switch(tenant),
        "group_allowed": bool(group_id and group_id in allowlist),
    }
    return payload


@router.get("/behavior")
async def get_behavior(tenant: str, limit: int = 100) -> dict[str, Any]:
    """读取行为聚合与已归因样本数；达到阈值后才会进入 Planner 提示。"""
    tenant = _tenant(tenant)
    limit = max(1, min(200, int(limit)))
    try:
        rows, attributed = await chat_repository.behavior_hints(tenant, limit=limit, minimum=0)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="聊天记忆数据库尚未就绪") from exc
    return {
        "items": [_behavior_payload(row) for row in rows],
        "attributed": attributed,
        "minimum": BEHAVIOR_MIN_SAMPLES,
    }


@router.delete("/behavior")
async def delete_behavior(tenant: str) -> dict[str, bool]:
    """清空指定租户的行为样本与聚合。"""
    tenant = _tenant(tenant)
    try:
        await chat_repository.clear_behavior(tenant)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="聊天记忆数据库尚未就绪") from exc
    return {"deleted": True}


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


@router.get("/expression")
async def list_expressions(tenant: str, status: str = "all") -> dict[str, Any]:
    """列出表达条目，可按 active/rejected/unchecked 过滤。"""
    tenant = _tenant(tenant)
    if status not in {"all", "active", "rejected", "unchecked"}:
        raise HTTPException(status_code=400, detail="status 只能是 all/active/rejected/unchecked")
    try:
        rows = await chat_repository.list_expressions(
            tenant,
            status=None if status == "all" else status,
            limit=1000,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="聊天记忆数据库尚未就绪") from exc
    return {"items": [_expression_payload(row) for row in rows]}


@router.patch("/expression/{expression_id}")
async def review_expression(expression_id: int, tenant: str, rejected: bool) -> dict[str, bool]:
    """人工通过或驳回一条表达；记录为已复审。"""
    tenant = _tenant(tenant)
    try:
        updated = await chat_repository.review_expression(tenant, expression_id, rejected)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="聊天记忆数据库尚未就绪") from exc
    if not updated:
        raise HTTPException(status_code=404, detail="表达条目不存在")
    return {"rejected": bool(rejected)}


@router.delete("/expression/{expression_id}")
async def delete_expression(expression_id: int, tenant: str) -> dict[str, bool]:
    """按租户删除一条表达。"""
    tenant = _tenant(tenant)
    try:
        deleted = await chat_repository.delete_expression(tenant, expression_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="聊天记忆数据库尚未就绪") from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="表达条目不存在")
    return {"deleted": True}
