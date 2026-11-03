"""聊天记忆控制台：租户、群风格与黑话。"""

from __future__ import annotations

import json
import base64
import asyncio
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
from ...chat.providers import provider_manager
from ...chat.memory.state import is_ready, degraded_reason
from ...chat.focus.manager import FAILURE_THRESHOLD, focus_mode_manager
from ...chat.message_adapter import load_images
from ...chat.memory.expression import (
    CHUNK_LENGTH,
    MAX_SOURCE_LENGTH,
    split_learning_text,
    extract_text_expressions,
)
from ...chat.memory.repository import chat_repository
from ...chat.behavior.reply_gate import (
    ReplyGate,
    talk_value_for,
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


def _json_dict(value: object) -> dict[str, Any]:
    """解析 JSON 对象；坏数据按空处理。"""
    try:
        items = json.loads(str(value or "{}"))
    except (TypeError, ValueError):
        return {}
    return items if isinstance(items, dict) else {}


def _tenant(value: str) -> str:
    """校验并归一化租户参数。"""
    tenant = str(value or "").strip()
    if not tenant:
        raise HTTPException(status_code=400, detail="tenant 不能为空")
    return tenant


def _trace_payload(row: object) -> dict[str, Any]:
    """把轮次追踪转成控制台结构。"""
    return {
        "id": getattr(row, "id", 0),
        "turn_id": getattr(row, "turn_id", ""),
        "trigger_kind": getattr(row, "trigger_kind", ""),
        "request_preview": getattr(row, "request_preview", ""),
        "injected": _json_dict(getattr(row, "injected", "{}")),
        "budget": _json_dict(getattr(row, "budget", "{}")),
        "provider": getattr(row, "provider", ""),
        "latency_ms": getattr(row, "latency_ms", 0),
        "error": getattr(row, "error", ""),
        "created_at": _iso(getattr(row, "created_at", None)),
    }


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
        "manual_checked": getattr(row, "manual_checked", False),
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


def _emoji_payload(row: object) -> dict[str, Any]:
    """把表情索引转成控制台结构，不暴露服务器路径。"""
    return {
        "hash": getattr(row, "hash", ""),
        "format": Path(str(getattr(row, "path", ""))).suffix.lstrip(".").lower(),
        "description": getattr(row, "description", ""),
        "tags": [str(item) for item in _json_list(getattr(row, "tags", "[]"))],
        "count": getattr(row, "count", 0),
        "last_used": _iso(getattr(row, "last_used", None)),
        "enabled": bool(getattr(row, "enabled", False)),
        "registered": bool(getattr(row, "registered", False)),
        "vlm_processed": bool(getattr(row, "vlm_processed", False)),
        "status": emoji_module.emoji_status(row),
        "register_time": _iso(getattr(row, "register_time", None)),
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
        tenants = await chat_repository.list_console_tenants()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="聊天数据库尚未就绪") from exc
    return {"tenants": tenants}


@router.get("/planner")
async def list_planner_samples(tenant: str, limit: int = 100) -> dict[str, Any]:
    """列出最近的 Planner 首轮动作样本，供校准与追溯。"""
    tenant = _tenant(tenant)
    limit = max(1, min(500, int(limit)))
    try:
        rows = await chat_repository.list_planner_samples(tenant, limit)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="聊天数据库尚未就绪") from exc
    return {"items": [_planner_payload(row) for row in rows]}


@router.get("/gate")
async def get_reply_gate(tenant: str, limit: int = 100) -> dict[str, Any]:
    """读取主动接话门控的观察窗口与当前开关。"""
    tenant = _tenant(tenant)
    limit = max(1, min(200, int(limit)))
    gate = default_reply_gate_registry.peek(tenant)
    payload = _gate_payload((gate or ReplyGate(tenant)).snapshot(limit))
    payload["proactive"] = {
        "talk_value": talk_value_for(tenant),
        "switch": await proactive_switch(tenant),
    }
    return payload


@router.get("/trace")
async def list_traces(tenant: str, limit: int = 100) -> dict[str, Any]:
    """列出保留策略内最近的轮次追踪。"""
    tenant = _tenant(tenant)
    limit = max(1, min(500, int(limit)))
    try:
        rows = await chat_repository.list_traces(tenant, limit)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="聊天数据库尚未就绪") from exc
    return {"items": [_trace_payload(row) for row in rows]}


@router.get("/style")
async def get_style(tenant: str) -> dict[str, Any]:
    """读取指定租户的群风格。"""
    tenant = _tenant(tenant)
    try:
        style = await chat_repository.get_style(tenant)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="聊天数据库尚未就绪") from exc
    return {"style": _style_payload(style) if style else None}


@router.put("/style")
async def save_style(payload: dict[str, Any]) -> dict[str, Any]:
    """保存人工风格覆盖；空文本表示取消覆盖。"""
    tenant = _tenant(str(payload.get("tenant") or ""))
    text = str(payload.get("manual_override") or "").strip()
    if len(text) > 200:
        raise HTTPException(status_code=400, detail="人工覆盖不能超过 200 字")
    try:
        style = await chat_repository.set_style_override(tenant, text)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="聊天数据库尚未就绪") from exc
    return {"style": _style_payload(style)}


@router.delete("/style")
async def delete_style(tenant: str) -> dict[str, bool]:
    """删除群风格；不存在时明确报错。"""
    tenant = _tenant(tenant)
    try:
        deleted = await chat_repository.delete_style(tenant)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="聊天数据库尚未就绪") from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="该租户没有群风格")
    return {"deleted": True}


@router.get("/jargon")
async def list_jargon(
    tenant: str,
    status: str = "all",
    search: str = "",
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    """分页列出黑话条目，可按状态和关键字过滤。"""
    tenant = _tenant(tenant)
    if status not in {"all", "active", "pending", "rejected"}:
        raise HTTPException(status_code=400, detail="status 只能是 all/active/pending/rejected")
    limit = max(1, min(200, int(limit)))
    offset = max(0, int(offset))
    selected = None if status == "all" else status
    try:
        rows = await chat_repository.list_jargon(
            tenant,
            status=selected,
            search=search,
            limit=limit,
            offset=offset,
        )
        total = await chat_repository.count_jargon(tenant, status=selected, search=search)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="聊天数据库尚未就绪") from exc
    return {"items": [_jargon_payload(row) for row in rows], "total": total}


@router.post("/jargon")
async def create_jargon(payload: dict[str, Any]) -> dict[str, Any]:
    """人工新增或覆盖一条黑话释义。"""
    tenant = _tenant(str(payload.get("tenant") or ""))
    term = payload.get("term")
    meaning = payload.get("meaning")
    if not isinstance(term, str) or not isinstance(meaning, str):
        raise HTTPException(status_code=400, detail="term 和 meaning 必须是字符串")
    try:
        row = await chat_repository.upsert_jargon(tenant, term, meaning)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="聊天数据库尚未就绪") from exc
    return {"item": _jargon_payload(row)}


@router.delete("/jargon/{jargon_id}")
async def delete_jargon(jargon_id: int, tenant: str) -> dict[str, bool]:
    """按租户删除一条黑话释义。"""
    tenant = _tenant(tenant)
    try:
        deleted = await chat_repository.delete_jargon(tenant, jargon_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="聊天数据库尚未就绪") from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="黑话条目不存在")
    return {"deleted": True}


@router.get("/expression")
async def list_expressions(
    tenant: str,
    status: str = "all",
    search: str = "",
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    """分页列出表达条目，可按状态和关键字过滤。"""
    tenant = _tenant(tenant)
    if status not in {"all", "active", "rejected", "unchecked"}:
        raise HTTPException(status_code=400, detail="status 只能是 all/active/rejected/unchecked")
    limit = max(1, min(200, int(limit)))
    offset = max(0, int(offset))
    selected = None if status == "all" else status
    try:
        rows = await chat_repository.list_expressions(
            tenant,
            status=selected,
            search=search,
            limit=limit,
            offset=offset,
        )
        total = await chat_repository.count_expressions(tenant, status=selected, search=search)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="聊天数据库尚未就绪") from exc
    return {"items": [_expression_payload(row) for row in rows], "total": total}


@router.post("/expression")
async def create_expression(payload: dict[str, Any]) -> dict[str, Any]:
    """人工新增或覆盖一条表达。"""
    tenant = _tenant(str(payload.get("tenant") or ""))
    situation = payload.get("situation")
    style = payload.get("style")
    if not isinstance(situation, str) or not isinstance(style, str):
        raise HTTPException(status_code=400, detail="situation 和 style 必须是字符串")
    try:
        rows = await chat_repository.upsert_expressions(tenant, [{"situation": situation, "style": style}])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="聊天数据库尚未就绪") from exc
    return {"item": _expression_payload(rows[0])}


@router.post("/expression/learn/prepare")
async def prepare_expression_learning(payload: dict[str, Any]) -> dict[str, Any]:
    """把粘贴文本切成可逐段提取的文段。"""
    text = payload.get("text")
    if not isinstance(text, str):
        raise HTTPException(status_code=400, detail="text 必须是字符串")
    if len(text) > MAX_SOURCE_LENGTH:
        raise HTTPException(status_code=400, detail=f"文段不能超过 {MAX_SOURCE_LENGTH:,} 字")
    try:
        chunks = split_learning_text(text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"chunks": chunks}


@router.post("/expression/learn/extract")
async def extract_expression_learning(payload: dict[str, Any]) -> dict[str, Any]:
    """调用 learner 模型从一段文本提取表达候选，不写入数据库。"""
    tenant = _tenant(str(payload.get("tenant") or ""))
    text = payload.get("text")
    if not isinstance(text, str) or not text.strip():
        raise HTTPException(status_code=400, detail="学习文段不能为空")
    if len(text) > CHUNK_LENGTH:
        raise HTTPException(status_code=400, detail="单段文段过长，请重新分段")
    try:
        candidates = await extract_text_expressions(text, tenant)
    except Exception:
        logger.opt(exception=True).warning(f"[ChatExpression] 文本学习失败 tenant={tenant}")
        raise HTTPException(status_code=502, detail="提取失败，请重试该段")
    return {
        "candidates": [
            {"situation": item.situation, "style": item.style, "evidence": item.evidence} for item in candidates
        ]
    }


@router.post("/expression/import")
async def import_expressions(payload: dict[str, Any]) -> dict[str, Any]:
    """把用户确认的候选写成人工确认表达。"""
    tenant = _tenant(str(payload.get("tenant") or ""))
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        raise HTTPException(status_code=400, detail="请选择要导入的候选")
    if len(items) > 200:
        raise HTTPException(status_code=400, detail="单次最多导入 200 条")
    clean: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            raise HTTPException(status_code=400, detail="候选格式不正确")
        situation = item.get("situation")
        style = item.get("style")
        if not isinstance(situation, str) or not isinstance(style, str):
            raise HTTPException(status_code=400, detail="候选格式不正确")
        clean.append({"situation": situation, "style": style})
    try:
        rows = await chat_repository.upsert_expressions(tenant, clean)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="聊天数据库尚未就绪") from exc
    return {"imported": len(rows), "items": [_expression_payload(row) for row in rows]}


@router.patch("/expression/{expression_id}")
async def review_expression(expression_id: int, tenant: str, rejected: bool) -> dict[str, bool]:
    """人工通过或驳回一条表达；记录为已复审。"""
    tenant = _tenant(tenant)
    try:
        updated = await chat_repository.review_expression(tenant, expression_id, rejected)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="聊天数据库尚未就绪") from exc
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
        raise HTTPException(status_code=503, detail="聊天数据库尚未就绪") from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="表达条目不存在")
    return {"deleted": True}


_EMOJI_STATUSES = {"registered", "pending", "unidentified", "disabled"}
_EMOJI_FORMATS = {"png", "jpg", "jpeg", "gif", "webp", "bmp"}
_EMOJI_SORTS = {"created_at", "register_time", "last_used", "count"}


@router.get("/emoji")
async def list_emoji(
    tenant: str,
    status: str = "",
    search: str = "",
    format: str = "",
    sort: str = "created_at",
    order: str = "desc",
    limit: int = 24,
    offset: int = 0,
) -> dict[str, Any]:
    """分页列出租户的表情包索引，可按状态、格式、关键字筛选并统计。"""
    tenant = _tenant(tenant)
    limit = max(1, min(200, int(limit)))
    offset = max(0, int(offset))
    selected_status = status if status in _EMOJI_STATUSES else ""
    selected_format = format.lower().lstrip(".") if format.lower().lstrip(".") in _EMOJI_FORMATS else ""
    try:
        rows = await chat_repository.list_emoji(
            tenant,
            status=selected_status,
            search=search,
            file_format=selected_format,
            sort=sort if sort in _EMOJI_SORTS else "created_at",
            descending=order != "asc",
            limit=limit,
            offset=offset,
        )
        counts = await chat_repository.count_emoji(tenant)
        total = await chat_repository.count_emoji_filtered(
            tenant,
            status=selected_status,
            search=search,
            file_format=selected_format,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="聊天数据库尚未就绪") from exc
    return {"items": [_emoji_payload(row) for row in rows], "counts": counts, "total": total}


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
    auto_described = False
    if not description:
        description, generated_tags = await _describe_upload(data)
        tags = tags or generated_tags
        auto_described = True
    try:
        row = await emoji_module.save_emoji(tenant, data, description=description, tags=tags)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="聊天数据库尚未就绪") from exc
    result: dict[str, Any] = {"item": _emoji_payload(row)}
    if auto_described and not description:
        result["warning"] = "视觉模型没能生成描述，表情已存为未识别，请手动补充"
    return result


@router.patch("/emoji/{emoji_hash}")
async def update_emoji(emoji_hash: str, tenant: str, payload: dict[str, Any]) -> dict[str, Any]:
    """启停、注册或修改一条表情。"""
    tenant = _tenant(tenant)
    for key in ("enabled", "registered"):
        if key in payload and not isinstance(payload[key], bool):
            raise HTTPException(status_code=400, detail=f"{key} 必须是布尔值")
    description = payload.get("description")
    if description is not None and not isinstance(description, str):
        raise HTTPException(status_code=400, detail="description 必须是字符串")
    tags = payload.get("tags")
    if tags is not None and (not isinstance(tags, list) or any(not isinstance(item, str) for item in tags)):
        raise HTTPException(status_code=400, detail="tags 必须是字符串数组")
    try:
        row = await chat_repository.get_emoji(tenant, emoji_hash)
        if row is None:
            raise HTTPException(status_code=404, detail="表情不存在")
        if "enabled" in payload:
            await chat_repository.set_emoji_enabled(tenant, emoji_hash, payload["enabled"])
        if "registered" in payload:
            await chat_repository.set_emoji_registered(tenant, emoji_hash, payload["registered"])
        if description is not None or tags is not None:
            row = await chat_repository.update_emoji_details(
                tenant,
                emoji_hash,
                description=description.strip() if description is not None else None,
                tags=[item.strip() for item in tags if item.strip()] if tags is not None else None,
            )
        else:
            row = await chat_repository.get_emoji(tenant, emoji_hash)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="聊天数据库尚未就绪") from exc
    if row is None:
        raise HTTPException(status_code=404, detail="表情不存在")
    return {"item": _emoji_payload(row)}


@router.delete("/emoji/{emoji_hash}")
async def delete_emoji(emoji_hash: str, tenant: str) -> dict[str, bool]:
    """删除表情索引与文件。"""
    tenant = _tenant(tenant)
    try:
        row = await chat_repository.delete_emoji(tenant, emoji_hash)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="聊天数据库尚未就绪") from exc
    if row is None:
        raise HTTPException(status_code=404, detail="表情不存在")
    emoji_module.remove_emoji_files(row.path, row.hash)
    return {"deleted": True}


@router.post("/emoji/batch-delete")
async def batch_delete_emoji(payload: dict[str, Any]) -> dict[str, int]:
    """批量删除表情索引与文件；单条失败继续处理其余。"""
    tenant = _tenant(str(payload.get("tenant") or ""))
    hashes = payload.get("hashes")
    if not isinstance(hashes, list) or not hashes:
        raise HTTPException(status_code=400, detail="hashes 不能为空")
    unique = list(dict.fromkeys(str(item).strip() for item in hashes if str(item).strip()))[:200]
    deleted = 0
    for emoji_hash in unique:
        try:
            row = await chat_repository.delete_emoji(tenant, emoji_hash)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail="聊天数据库尚未就绪") from exc
        if row is None:
            continue
        emoji_module.remove_emoji_files(row.path, row.hash)
        deleted += 1
    return {"deleted": deleted}


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
    """返回聊天存储、调度器与在线 Bot 的可用状态。"""
    try:
        chat = get_config_manager().get_system()["chat"]
        enabled = bool(chat.get("enabled"))
    except Exception:
        enabled = False
        chat = {"enabled": False}
    from ...chat.work_schedule import schedule_status

    work_schedule = schedule_status(chat)
    focus_groups = []
    focus_ok = True
    try:
        focus_groups = focus_mode_manager.status_snapshot()
        focus_ok = all(
            group["worker_alive"] and group["consecutive_failures"] <= FAILURE_THRESHOLD for group in focus_groups
        )
    except Exception:
        focus_groups = []
        logger.opt(exception=True).debug("[ChatFocus] 健康状态读取失败")
    return {
        "enabled": enabled,
        "work_schedule": work_schedule,
        "storage_ready": is_ready(),
        "degraded_reason": degraded_reason(),
        "scheduler_ready": bool(_scheduler_jobs()),
        "online_bots": len(get_bots()),
        "focus_groups": focus_groups,
        "focus_ok": focus_ok,
    }


image_router = APIRouter(prefix="/chat", tags=["fxbot-chat"], dependencies=[Depends(sse_auth)])


@image_router.get("/emoji/{emoji_hash}/image")
async def emoji_image(emoji_hash: str, tenant: str) -> FileResponse:
    """返回表情原图，供控制台预览；支持查询参数 token。"""
    tenant = _tenant(tenant)
    try:
        row = await chat_repository.get_emoji(tenant, emoji_hash)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="聊天数据库尚未就绪") from exc
    if row is None or not row.path or not Path(row.path).is_file():
        raise HTTPException(status_code=404, detail="表情不存在")
    return FileResponse(row.path)


@image_router.get("/emoji/{emoji_hash}/thumbnail")
async def emoji_thumbnail(emoji_hash: str, tenant: str) -> FileResponse:
    """返回表情缩略图；生成失败时回退原图。"""
    tenant = _tenant(tenant)
    try:
        row = await chat_repository.get_emoji(tenant, emoji_hash)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="聊天数据库尚未就绪") from exc
    if row is None or not row.path or not Path(row.path).is_file():
        raise HTTPException(status_code=404, detail="表情不存在")
    try:
        target = await asyncio.to_thread(emoji_module.emoji_thumbnail, emoji_hash, row.path)
    except Exception:
        logger.opt(exception=True).debug("[Chat] 表情缩略图生成失败，回退原图")
        return FileResponse(row.path)
    return FileResponse(target)
