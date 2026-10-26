"""长期记忆抽取批次：选批、调用模型、事务提交与退避。"""

from __future__ import annotations

import asyncio
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
from .repository import TenantJobState, chat_repository

_MAX_FAILURES = 5
_BACKFILL_INTERVAL_SECONDS = 10
_tick_lock: asyncio.Lock | None = None


def _extract_model() -> str:
    """读取抽取专用模型名；未配置时用 Provider 默认模型。"""
    try:
        routing = get_config_manager().get_system()["chat"].get("model_routing") or {}
        return str(routing.get("extract") or "")
    except Exception:
        return ""


def _tick_guard() -> asyncio.Lock:
    """实时与回填任务共用一把锁，避免同一租户被并发领取。"""
    global _tick_lock
    if _tick_lock is None:
        _tick_lock = asyncio.Lock()
    return _tick_lock


def _batch_id(tenant: str, job: str, range_start: int, range_end: int, algorithm_version: int) -> str:
    """同一租户、任务和消息范围得到稳定批次键。"""
    return hashlib.sha1(f"{tenant}|{job}|{range_start}|{range_end}|{algorithm_version}".encode("utf-8")).hexdigest()


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


def _filter_clean(messages: list[ChatMessage], tenant: str, *, check_duplicate: bool = True) -> list[ChatMessage]:
    """按内容噪声过滤，并去掉本批内完全相同的消息。"""
    clean: list[ChatMessage] = []
    seen: set[str] = set()
    for message in messages:
        content = " ".join(str(message.content or "").split())
        if content in seen or not should_learn(content, tenant=tenant, check_duplicate=check_duplicate):
            continue
        seen.add(content)
        clean.append(message)
    return clean


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


async def _mark_failed(
    tenant: str,
    job: str,
    range_start: int,
    range_end: int,
    algorithm_version: int,
    error: str,
    direction: str,
) -> None:
    """记录失败并退避；连续失败达到上限时告警并让调度跳过。"""
    await chat_repository.mark_batch_failed(
        tenant,
        range_start,
        range_end,
        job=job,
        algorithm_version=algorithm_version,
        error=error,
        direction=direction,
    )
    cursor = await chat_repository.get_cursor(tenant, job)
    if cursor is not None and cursor.fail_count >= _MAX_FAILURES:
        logger.error(f"[ChatMemory] {job} 连续失败 {cursor.fail_count} 次，已跳过租户: {tenant}")


async def process_tenant(
    tenant: str, *, provider: Any = None, now: datetime | None = None, cfg: dict[str, Any] | None = None
) -> bool:
    """处理一个租户至多一个实时批次；成功提交返回 True。"""
    if not is_ready():
        return False
    config = cfg or memory_config()
    state = await chat_repository.get_session_state(tenant)
    if not tenant_learn_enabled(state):
        return False
    cursor = await chat_repository.get_cursor(tenant)
    if cursor is not None and cursor.fail_count >= _MAX_FAILURES:
        return False
    moment = now or utc_now_naive()
    if cursor is not None and cursor.next_retry_at is not None and cursor.next_retry_at > moment:
        return False
    after_id = int(cursor.cursor_seq) if cursor else 0
    raw = await chat_repository.messages_after(
        tenant, after_id, limit=max(int(config["episode_max_messages"]) * 2, int(config["episode_min_messages"]) * 2)
    )
    if not raw:
        return False
    clean = _filter_clean(raw, tenant)
    if not _ready(raw, clean, moment, config):
        return False

    algorithm_version = int(config["algorithm_version"])
    if len(clean) < int(config["episode_min_messages"]):
        # 整批都是噪声：记录 skipped 并推进游标，避免原地打转
        high_water = int(raw[-1].id or after_id)
        batch_id = _batch_id(tenant, "memory", int(raw[0].id or 0), high_water, algorithm_version)
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
    batch_id = _batch_id(tenant, "memory", int(included[0].id or 0), high_water, algorithm_version)
    result = await _call_model(tenant, included, provider)
    if result is None:
        await _mark_failed(
            tenant,
            "memory",
            int(included[0].id or after_id),
            high_water,
            algorithm_version,
            "抽取失败",
            "forward",
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
        await _mark_failed(
            tenant,
            "memory",
            int(included[0].id or after_id),
            high_water,
            algorithm_version,
            f"{type(exc).__name__}: {exc}",
            "forward",
        )
        return False
    mark_learned([message.content for message in included], tenant=tenant)
    return True


async def process_backfill(
    tenant: str, *, provider: Any = None, now: datetime | None = None, cfg: dict[str, Any] | None = None
) -> bool:
    """从新到旧处理一个回填批次；成功提交返回 True。"""
    if not is_ready():
        return False
    config = cfg or memory_config()
    state = await chat_repository.get_session_state(tenant)
    if state is None or state.backfilled_at is not None or not tenant_learn_enabled(state):
        return False
    cursor = await chat_repository.get_cursor(tenant, "backfill")
    if cursor is None or cursor.fail_count >= _MAX_FAILURES:
        return False
    moment = now or utc_now_naive()
    if cursor.next_retry_at is not None and cursor.next_retry_at > moment:
        return False
    batch_size = max(1, int(config["backfill_batch"]))
    done = await chat_repository.done_batch_count(tenant, "backfill")
    if done and (moment - cursor.updated_at).total_seconds() < _BACKFILL_INTERVAL_SECONDS:
        return False
    if cursor.cursor_seq <= cursor.stop_seq + 1:
        await chat_repository.finish_backfill(tenant)
        return False
    raw = await chat_repository.messages_between(
        tenant,
        cursor.stop_seq,
        cursor.cursor_seq,
        limit=max(batch_size * 4, 80),
    )
    if not raw:
        await chat_repository.finish_backfill(tenant)
        return False

    algorithm_version = int(config["algorithm_version"])
    clean = _filter_clean(raw, tenant, check_duplicate=False)
    if not clean:
        oldest = int(raw[-1].id or cursor.cursor_seq)
        next_cursor = max(int(cursor.stop_seq), oldest)
        batch_id = _batch_id(
            tenant,
            "backfill",
            int(raw[-1].id or 0),
            int(raw[0].id or 0),
            algorithm_version,
        )
        try:
            await chat_repository.commit_extraction(
                tenant,
                batch_id,
                raw,
                int(raw[0].id or 0),
                ExtractionResult(topics=(), facts=()),
                job="backfill",
                algorithm_version=algorithm_version,
                direction="backward",
                next_cursor=next_cursor,
            )
        except Exception as exc:
            logger.opt(exception=True).warning("[ChatMemory] 回填噪声批次提交失败，游标不推进")
            await _mark_failed(
                tenant,
                "backfill",
                int(raw[-1].id or 0),
                int(raw[0].id or 0),
                algorithm_version,
                f"{type(exc).__name__}: {exc}",
                "backward",
            )
            return False
        if next_cursor <= cursor.stop_seq + 1:
            await chat_repository.finish_backfill(tenant)
        return False

    selected = clean[:batch_size]
    included = list(reversed(selected))
    high_water = int(selected[0].id or cursor.cursor_seq)
    next_cursor = max(int(cursor.stop_seq), int(selected[-1].id or cursor.cursor_seq))
    batch_id = _batch_id(
        tenant,
        "backfill",
        int(selected[-1].id or 0),
        high_water,
        algorithm_version,
    )
    result = await _call_model(tenant, included, provider)
    if result is None:
        await _mark_failed(
            tenant,
            "backfill",
            int(selected[-1].id or 0),
            high_water,
            algorithm_version,
            "回填抽取失败",
            "backward",
        )
        return False
    try:
        await chat_repository.commit_extraction(
            tenant,
            batch_id,
            included,
            high_water,
            result,
            job="backfill",
            algorithm_version=algorithm_version,
            direction="backward",
            next_cursor=next_cursor,
            source="backfill",
        )
    except Exception as exc:
        logger.opt(exception=True).warning("[ChatMemory] 回填事务提交失败，游标不推进")
        await _mark_failed(
            tenant,
            "backfill",
            int(selected[-1].id or 0),
            high_water,
            algorithm_version,
            f"{type(exc).__name__}: {exc}",
            "backward",
        )
        return False
    if next_cursor <= cursor.stop_seq + 1:
        await chat_repository.finish_backfill(tenant)
    return True


def _learn_enabled(candidate: TenantJobState, cfg: dict[str, Any]) -> bool:
    """租户显式开关优先，否则跟随本轮读取到的全局配置。"""
    if candidate.learn_enabled is not None:
        return bool(candidate.learn_enabled)
    return bool(cfg["learn_enabled"])


def _tenant_failed(candidate: TenantJobState) -> bool:
    """任一任务连续失败达到上限后跳过整个租户，避免继续占用名额。"""
    return any(
        cursor is not None and cursor.fail_count >= _MAX_FAILURES for cursor in (candidate.memory, candidate.backfill)
    )


def _memory_due(candidate: TenantJobState, now: datetime) -> bool:
    """实时游标落后于最新消息且不在退避中。"""
    processed = int(candidate.memory.cursor_seq) if candidate.memory else 0
    if candidate.last_message_id <= processed:
        return False
    retry = candidate.memory.next_retry_at if candidate.memory else None
    return retry is None or retry <= now


def _backfill_due(candidate: TenantJobState, now: datetime, cfg: dict[str, Any]) -> bool:
    """回填尚未完成，且不在退避或 10 秒节流中。"""
    if candidate.backfilled_at is not None:
        return False
    if candidate.message_count < int(cfg["episode_min_messages"]):
        return False
    cursor = candidate.backfill
    if cursor is None:
        return True
    if cursor.fail_count >= _MAX_FAILURES:
        return False
    if cursor.next_retry_at is not None and cursor.next_retry_at > now:
        return False
    if cursor.cursor_seq <= cursor.stop_seq + 1:
        return True
    return not candidate.backfill_done or (now - cursor.updated_at).total_seconds() >= _BACKFILL_INTERVAL_SECONDS


def _schedule_key(
    candidate: TenantJobState, now: datetime, memory_due: bool, backfill_due: bool
) -> tuple[datetime, bool, datetime, str]:
    """先按可重试时间，再按最久未运行排序；从未运行过的租户排在最前。"""
    moments: list[datetime] = []
    if memory_due:
        moments.append(candidate.memory.next_retry_at if candidate.memory and candidate.memory.next_retry_at else now)
    if backfill_due:
        moments.append(
            candidate.backfill.next_retry_at if candidate.backfill and candidate.backfill.next_retry_at else now
        )
    runs = [cursor.updated_at for cursor in (candidate.memory, candidate.backfill) if cursor is not None]
    last_run = min(runs) if runs else None
    return (
        min(moments) if moments else now,
        last_run is not None,
        last_run or now,
        candidate.tenant,
    )


def _eligible(
    snapshot: list[TenantJobState], cfg: dict[str, Any], now: datetime
) -> list[tuple[tuple[datetime, bool, datetime, str], TenantJobState, bool, bool]]:
    """过滤禁用、失败与退避中的租户，并按公平顺序排列。"""
    result = []
    for candidate in snapshot:
        if not _learn_enabled(candidate, cfg) or _tenant_failed(candidate):
            continue
        memory_due = _memory_due(candidate, now)
        backfill_due = _backfill_due(candidate, now, cfg)
        if not memory_due and not backfill_due:
            continue
        result.append((_schedule_key(candidate, now, memory_due, backfill_due), candidate, memory_due, backfill_due))
    result.sort(key=lambda item: item[0])
    return result


def _cursor_changed(before: Any, after: Any) -> bool:
    """判断实时任务本轮是否真的写了批次或游标。"""
    if before is None:
        return after is not None
    if after is None:
        return False
    return (before.cursor_seq, before.fail_count, before.updated_at) != (
        after.cursor_seq,
        after.fail_count,
        after.updated_at,
    )


async def _ensure_backfill(tenant: str, cfg: dict[str, Any]) -> None:
    """首次学习时冻结历史窗口；失败只记日志，不阻塞其它租户。"""
    try:
        await chat_repository.ensure_backfill(
            tenant,
            min_messages=int(cfg["episode_min_messages"]),
            max_messages=int(cfg["backfill_messages"]),
        )
    except Exception:
        logger.opt(exception=True).warning(f"[ChatMemory] 回填窗口初始化失败: {tenant}")


async def memory_job_tick(
    *, provider: Any = None, now: datetime | None = None, cfg: dict[str, Any] | None = None
) -> int:
    """实时任务：每租户每轮最多一个批次，成功后本轮不再回填。"""
    config = cfg or memory_config()
    moment = now or utc_now_naive()
    async with _tick_guard():
        try:
            snapshot = await chat_repository.scheduling_snapshot()
        except Exception:
            logger.opt(exception=True).debug("[ChatMemory] 读取待抽取租户失败")
            return 0
        processed = 0
        for _, candidate, _, backfill_due in _eligible(snapshot, config, moment):
            if processed >= int(config["max_tenants_per_tick"]):
                break
            tenant = candidate.tenant
            try:
                if candidate.backfilled_at is None:
                    await _ensure_backfill(tenant, config)
                before = await chat_repository.get_cursor(tenant, "memory")
                worked = await process_tenant(tenant, provider=provider, now=moment, cfg=config)
                if not worked:
                    worked = _cursor_changed(before, await chat_repository.get_cursor(tenant, "memory"))
                if not worked and backfill_due:
                    worked = await process_backfill(tenant, provider=provider, now=moment, cfg=config)
                if worked:
                    processed += 1
            except Exception:
                logger.opt(exception=True).warning(f"[ChatMemory] 租户批次处理失败: {tenant}")
        return processed


async def backfill_job_tick(
    *, provider: Any = None, now: datetime | None = None, cfg: dict[str, Any] | None = None
) -> int:
    """回填任务：每 10 秒最多为每个租户推进一个批次。"""
    config = cfg or memory_config()
    moment = now or utc_now_naive()
    async with _tick_guard():
        try:
            snapshot = await chat_repository.scheduling_snapshot()
        except Exception:
            logger.opt(exception=True).debug("[ChatMemory] 读取待回填租户失败")
            return 0
        candidates = [
            candidate for _, candidate, _, backfill_due in _eligible(snapshot, config, moment) if backfill_due
        ]
        processed = 0
        for candidate in candidates:
            if processed >= int(config["max_tenants_per_tick"]):
                break
            tenant = candidate.tenant
            try:
                if candidate.backfilled_at is None and candidate.backfill is None:
                    await _ensure_backfill(tenant, config)
                if await process_backfill(tenant, provider=provider, now=moment, cfg=config):
                    processed += 1
            except Exception:
                logger.opt(exception=True).warning(f"[ChatMemory] 回填批次处理失败: {tenant}")
        return processed
