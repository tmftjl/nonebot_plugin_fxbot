"""聊天记忆控制台：租户、群风格与黑话。"""

from __future__ import annotations

import json
import base64
import binascii
from typing import Any
from pathlib import Path
from datetime import datetime

from fastapi import Depends, APIRouter, HTTPException
from nonebot import logger, get_bots
from fastapi.responses import FileResponse

from ..auth import sse_auth, bearer_auth
from ...chat import emoji as emoji_module
from ...config import get_manager as get_config_manager
from ...utils.tz import SHANGHAI_TZ, as_shanghai
from ...chat.vision import describe_emoji
from ...chat.knowledge import import_document
from ...chat.providers import provider_manager
from ...chat.memory.state import is_ready, degraded_reason
from ...chat.message_adapter import load_images
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


def _reminder_payload(row: object) -> dict[str, Any]:
    """把提醒转成控制台结构。"""
    return {
        "id": getattr(row, "id", 0),
        "user_id": getattr(row, "user_id", ""),
        "content": getattr(row, "content", ""),
        "due_at": _iso(getattr(row, "due_at", None)),
        "status": getattr(row, "status", ""),
        "attempts": getattr(row, "attempts", 0),
        "next_attempt_at": _iso(getattr(row, "next_attempt_at", None)),
        "last_error": getattr(row, "last_error", ""),
        "sent_at": _iso(getattr(row, "sent_at", None)),
        "created_at": _iso(getattr(row, "created_at", None)),
    }


def _cursor_payload(row: object) -> dict[str, Any]:
    """把 job 游标转成控制台结构。"""
    return {
        "job": getattr(row, "job", ""),
        "direction": getattr(row, "direction", ""),
        "cursor_seq": getattr(row, "cursor_seq", 0),
        "fail_count": getattr(row, "fail_count", 0),
        "next_retry_at": _iso(getattr(row, "next_retry_at", None)),
        "last_error": getattr(row, "last_error", ""),
        "updated_at": _iso(getattr(row, "updated_at", None)),
    }


def _doc_payload(row: object) -> dict[str, Any]:
    """把知识文档转成控制台结构。"""
    return {
        "id": getattr(row, "id", 0),
        "title": getattr(row, "title", ""),
        "source": getattr(row, "source", ""),
        "status": getattr(row, "status", ""),
        "hash": getattr(row, "hash", ""),
        "created_at": _iso(getattr(row, "created_at", None)),
        "updated_at": _iso(getattr(row, "updated_at", None)),
    }


def _emoji_payload(row: object) -> dict[str, Any]:
    """把表情索引转成控制台结构，不暴露服务器路径。"""
    return {
        "hash": getattr(row, "hash", ""),
        "description": getattr(row, "description", ""),
        "tags": [str(item) for item in _json_list(getattr(row, "tags", "[]"))],
        "count": getattr(row, "count", 0),
        "last_used": _iso(getattr(row, "last_used", None)),
        "enabled": bool(getattr(row, "enabled", False)),
        "created_at": _iso(getattr(row, "created_at", None)),
    }


def _decode_upload(content: object) -> bytes:
    """解析 base64 或 data URL 上传内容。"""
    value = str(content or "").strip()
    if value.startswith("data:"):
        _, _, value = value.partition(",")
    if not value:
        raise ValueError("缺少图片内容")
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("图片内容不是有效的 base64") from exc


async def _describe_upload(data: bytes) -> tuple[str, list[str]]:
    """用视觉模型生成表情描述与标签；失败时返回空结果。"""
    try:
        images = await load_images([data])
        if not images:
            return "", []
        async with provider_manager.lease() as provider:
            return await describe_emoji(provider, images[0])
    except Exception:
        logger.opt(exception=True).warning("[Chat] 表情描述生成失败")
        return "", []


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


@router.get("/emoji")
async def list_emoji(tenant: str) -> dict[str, Any]:
    """列出租户的表情包索引。"""
    tenant = _tenant(tenant)
    try:
        rows = await chat_repository.list_emoji(tenant)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="聊天记忆数据库尚未就绪") from exc
    return {"items": [_emoji_payload(row) for row in rows]}


@router.post("/emoji")
async def upload_emoji(payload: dict[str, Any]) -> dict[str, Any]:
    """上传表情；未提供描述时交给视觉模型生成。"""
    tenant = _tenant(str(payload.get("tenant") or ""))
    try:
        data = _decode_upload(payload.get("content"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    description = str(payload.get("description") or "").strip()
    tags = [str(item).strip() for item in payload.get("tags") or [] if str(item).strip()]
    if not description:
        description, generated_tags = await _describe_upload(data)
        tags = tags or generated_tags
    try:
        row = await emoji_module.save_emoji(tenant, data, description=description, tags=tags)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="聊天记忆数据库尚未就绪") from exc
    return {"item": _emoji_payload(row)}


@router.patch("/emoji/{emoji_hash}")
async def set_emoji_enabled(emoji_hash: str, tenant: str, enabled: bool) -> dict[str, bool]:
    """启停一条表情。"""
    tenant = _tenant(tenant)
    try:
        updated = await chat_repository.set_emoji_enabled(tenant, emoji_hash, enabled)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="聊天记忆数据库尚未就绪") from exc
    if not updated:
        raise HTTPException(status_code=404, detail="表情不存在")
    return {"enabled": bool(enabled)}


@router.delete("/emoji/{emoji_hash}")
async def delete_emoji(emoji_hash: str, tenant: str) -> dict[str, bool]:
    """删除表情索引与文件。"""
    tenant = _tenant(tenant)
    try:
        row = await chat_repository.delete_emoji(tenant, emoji_hash)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="聊天记忆数据库尚未就绪") from exc
    if row is None:
        raise HTTPException(status_code=404, detail="表情不存在")
    emoji_module.remove_file(row.path)
    return {"deleted": True}


@router.get("/knowledge")
async def list_knowledge(tenant: str) -> dict[str, Any]:
    """列出租户的知识文档。"""
    tenant = _tenant(tenant)
    try:
        rows = await chat_repository.list_knowledge_docs(tenant)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="聊天记忆数据库尚未就绪") from exc
    return {"items": [_doc_payload(row) for row in rows]}


@router.post("/knowledge")
async def upload_knowledge(payload: dict[str, Any]) -> dict[str, Any]:
    """导入纯文本或 Markdown；切块后整份生效。"""
    tenant = _tenant(str(payload.get("tenant") or ""))
    try:
        doc = await import_document(
            tenant,
            str(payload.get("title") or ""),
            str(payload.get("source") or "控制台"),
            str(payload.get("content") or ""),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="聊天记忆数据库尚未就绪") from exc
    return {"item": _doc_payload(doc)}


@router.delete("/knowledge/{doc_id}")
async def delete_knowledge(doc_id: int, tenant: str) -> dict[str, bool]:
    """删除知识文档；切片不再参与检索。"""
    tenant = _tenant(tenant)
    try:
        deleted = await chat_repository.delete_knowledge_doc(tenant, doc_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="聊天记忆数据库尚未就绪") from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="知识文档不存在")
    return {"deleted": True}


@router.get("/reminders")
async def list_reminders(tenant: str, limit: int = 100) -> dict[str, Any]:
    """列出提醒及重试状态，供控制台排查未送达。"""
    tenant = _tenant(tenant)
    limit = max(1, min(500, int(limit)))
    try:
        rows = await chat_repository.list_reminders(tenant, limit=limit)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="聊天记忆数据库尚未就绪") from exc
    return {"items": [_reminder_payload(row) for row in rows]}


def _scheduler_jobs() -> list[dict[str, Any]]:
    """读取已注册的后台任务；调度器未加载时返回空列表。"""
    try:
        from nonebot_plugin_apscheduler import scheduler

        jobs = scheduler.get_jobs()
    except Exception:
        return []
    result = []
    for job in jobs:
        if not str(job.id).startswith("fxbot_chat_"):
            continue
        next_run = getattr(job, "next_run_time", None)
        result.append(
            {
                "id": str(job.id),
                "next_run": next_run.isoformat() if next_run else None,
                "pending": bool(getattr(job, "pending", False)),
            }
        )
    return sorted(result, key=lambda item: item["id"])


@router.get("/health")
async def chat_health() -> dict[str, Any]:
    """返回聊天记忆、调度器与在线 Bot 的可用状态。"""
    try:
        enabled = bool(get_config_manager().get_system()["chat"].get("enabled"))
    except Exception:
        enabled = False
    return {
        "enabled": enabled,
        "memory_ready": is_ready(),
        "degraded_reason": degraded_reason(),
        "scheduler_ready": bool(_scheduler_jobs()),
        "online_bots": len(get_bots()),
    }


@router.get("/jobs")
async def chat_jobs(tenant: str = "") -> dict[str, Any]:
    """返回调度任务与租户游标，游标读取失败时仍返回任务列表。"""
    cursors: list[dict[str, Any]] = []
    if tenant.strip():
        try:
            rows = await chat_repository.list_cursors(_tenant(tenant))
            cursors = [_cursor_payload(row) for row in rows]
        except RuntimeError:
            cursors = []
    return {"jobs": _scheduler_jobs(), "cursors": cursors}


image_router = APIRouter(prefix="/chat", tags=["fxbot-chat"], dependencies=[Depends(sse_auth)])


@image_router.get("/emoji/{emoji_hash}/image")
async def emoji_image(emoji_hash: str, tenant: str) -> FileResponse:
    """返回表情原图，供控制台预览；支持查询参数 token。"""
    tenant = _tenant(tenant)
    try:
        row = await chat_repository.get_emoji(tenant, emoji_hash)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="聊天记忆数据库尚未就绪") from exc
    if row is None or not row.path or not Path(row.path).is_file():
        raise HTTPException(status_code=404, detail="表情不存在")
    return FileResponse(row.path)
