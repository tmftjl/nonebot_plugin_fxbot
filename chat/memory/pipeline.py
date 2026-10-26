"""长期记忆抽取批次：选批、调用模型、事务提交与退避。"""

from __future__ import annotations

import hashlib
from typing import Any
from datetime import datetime

from nonebot import logger

from .state import is_ready
from .config import memory_config, tenant_learn_enabled
from .models import ChatMessage, utc_now_naive
from .extract import ExtractionResult, request_extraction
from ...config import get_manager as get_config_manager
from ..bindings import PROVIDER, get_binding
from .filtering import mark_learned, should_learn
from ..providers import provider_manager
from .repository import chat_repository


def _extract_model() -> str:
    """读取抽取专用模型名；未配置时用 Provider 默认模型。"""
    try:
        routing = get_config_manager().get_system()["chat"].get("model_routing") or {}
        return str(routing.get("extract") or "")
    except Exception:
        return ""


def _ready(raw: list[ChatMessage], clean: list[ChatMessage], now: datetime, cfg: dict[str, Any]) -> bool:
    """按空闲、条数、跨度判断是否切批；至少要有最小条数的干净消息。"""
    if len(clean) < int(cfg["episode_min_messages"]):
        # 只有整批都是噪声时才越过；有干净消息但不够数就等下一轮
        return not clean and len(raw) >= int(cfg["episode_min_messages"])
    if len(clean) >= int(cfg["episode_max_messages"]):
        return True
    span = (clean[-1].occurred_at or clean[-1].ingested_at) - (clean[0].occurred_at or clean[0].ingested_at)
    if span.total_seconds() >= int(cfg["episode_max_hours"]) * 3600:
        return True
    idle = now - (clean[-1].occurred_at or clean[-1].ingested_at)
    return idle.total_seconds() >= int(cfg["episode_idle_minutes"]) * 60


def _take_budget(clean: list[ChatMessage], limit: int) -> list[ChatMessage]:
    """按字符预算取完整纳入的最后一条作为高水位。"""
    included: list[ChatMessage] = []
    used = 0
    for message in clean:
        size = len(message.content or "")
        if included and used + size > limit:
            break
        included.append(message)
        used += size
    return included


async def _call_model(tenant: str, messages: list[ChatMessage], provider: Any) -> ExtractionResult | None:
    """在事务外调用抽取模型。"""
    model = _extract_model()
    if provider is not None:
        return await request_extraction(provider, messages, model=model)
    try:
        provider_name = get_binding(PROVIDER, tenant)
        if not provider_name:
            provider_name = str(get_config_manager().get_system()["chat"].get("provider") or "")
        async with provider_manager.lease(provider_name) as leased:
            return await request_extraction(leased, messages, model=model)
    except Exception as exc:
        logger.opt(exception=True).warning(f"[ChatMemory] 抽取 Provider 不可用: {type(exc).__name__}")
        return None


async def process_tenant(
    tenant: str, *, provider: Any = None, now: datetime | None = None, cfg: dict[str, Any] | None = None
) -> bool:
    """处理一个租户至多一个批次；成功提交返回 True。"""
    if not is_ready():
        return False
    config = cfg or memory_config()
    state = await chat_repository.get_session_state(tenant)
    if not tenant_learn_enabled(state):
        return False
    cursor = await chat_repository.get_cursor(tenant)
    moment = now or utc_now_naive()
    if cursor is not None and cursor.next_retry_at is not None and cursor.next_retry_at > moment:
        return False
    after_id = int(cursor.cursor_seq) if cursor else 0
    raw = await chat_repository.messages_after(
        tenant, after_id, limit=max(int(config["episode_max_messages"]) * 2, int(config["episode_min_messages"]) * 2)
    )
    if not raw:
        return False
    clean: list[ChatMessage] = []
    seen: set[str] = set()
    for message in raw:
        content = " ".join(str(message.content or "").split())
        if content in seen or not should_learn(content, tenant=tenant):
            continue
        seen.add(content)
        clean.append(message)
    if not _ready(raw, clean, moment, config):
        return False

    algorithm_version = int(config["algorithm_version"])
    if len(clean) < int(config["episode_min_messages"]):
        # 整批都是噪声：记录 skipped 并推进游标，避免原地打转
        high_water = int(raw[-1].id or after_id)
        batch_id = hashlib.sha1(
            f"{tenant}|memory|{int(raw[0].id or 0)}|{high_water}|{algorithm_version}".encode("utf-8")
        ).hexdigest()
        await chat_repository.commit_extraction(
            tenant,
            batch_id,
            raw,
            high_water,
            ExtractionResult(topics=(), facts=()),
            algorithm_version=algorithm_version,
        )
        return False

    included = _take_budget(clean, int(config["max_input_chars_per_batch"]))
    if not included:
        return False
    high_water = int(included[-1].id or after_id)
    batch_id = hashlib.sha1(
        f"{tenant}|memory|{int(included[0].id or 0)}|{high_water}|{algorithm_version}".encode("utf-8")
    ).hexdigest()
    result = await _call_model(tenant, included, provider)
    if result is None:
        await chat_repository.mark_batch_failed(
            tenant,
            int(included[0].id or after_id),
            high_water,
            algorithm_version=algorithm_version,
            error="抽取失败",
        )
        return False
    try:
        await chat_repository.commit_extraction(
            tenant,
            batch_id,
            included,
            high_water,
            result,
            algorithm_version=algorithm_version,
        )
    except Exception as exc:
        logger.opt(exception=True).warning("[ChatMemory] 抽取事务提交失败，游标不推进")
        await chat_repository.mark_batch_failed(
            tenant,
            int(included[0].id or after_id),
            high_water,
            algorithm_version=algorithm_version,
            error=f"{type(exc).__name__}: {exc}",
        )
        return False
    mark_learned([message.content for message in included], tenant=tenant)
    return True


async def memory_job_tick(*, provider: Any = None, now: datetime | None = None) -> int:
    """后台扫描；每个 tick 最多处理配置数量的租户。"""
    cfg = memory_config()
    try:
        tenants = await chat_repository.pending_tenants(limit=max(10, int(cfg["max_tenants_per_tick"]) * 4))
    except Exception:
        logger.opt(exception=True).debug("[ChatMemory] 读取待抽取租户失败")
        return 0
    processed = 0
    for tenant in tenants:
        if processed >= int(cfg["max_tenants_per_tick"]):
            break
        try:
            if await process_tenant(tenant, provider=provider, now=now, cfg=cfg):
                processed += 1
        except Exception:
            logger.opt(exception=True).warning(f"[ChatMemory] 租户批次处理失败: {tenant}")
    return processed
