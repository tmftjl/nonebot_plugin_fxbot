"""模型任务的分钟级调用统计；口径对齐 MaiBot `model_task_usage_service`。

只记录调用次数、失败次数与成功耗时，不保存请求内容；汇总保留七天。
"""

from __future__ import annotations

import asyncio
from time import perf_counter
from typing import Any
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from nonebot import logger
from sqlmodel import Field, SQLModel, select
from sqlalchemy import func, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ...db import with_session
from ...utils.tz import now, to_naive_utc

RETENTION_DAYS = 7
_MAX_MODELS = 20


class ModelTaskUsage(SQLModel, table=True):
    """按分钟聚合的模型调用汇总；主键对齐 MaiBot，不含自增 id。"""

    __tablename__ = "model_task_usage"

    bucket_time: datetime = Field(primary_key=True)
    task_name: str = Field(primary_key=True, max_length=100)
    model_name: str = Field(primary_key=True, max_length=255)
    model_identifier: str = Field(primary_key=True, max_length=255)
    provider_name: str = Field(primary_key=True, max_length=255)
    request_count: int = Field(default=0)
    failure_count: int = Field(default=0)
    success_duration: float = Field(default=0.0)


class _UsageRepository:
    """统计写入与聚合查询。"""

    @with_session
    async def record(
        self,
        session: AsyncSession,
        task_name: str,
        model_name: str,
        model_identifier: str,
        provider_name: str,
        duration: float,
        succeeded: bool,
    ) -> None:
        """原子累加分钟桶，并顺带清理七天前的汇总。"""
        bucket = to_naive_utc(now()).replace(second=0, microsecond=0)
        statement = sqlite_insert(ModelTaskUsage).values(
            bucket_time=bucket,
            task_name=task_name,
            model_name=model_name,
            model_identifier=model_identifier,
            provider_name=provider_name,
            request_count=1,
            failure_count=0 if succeeded else 1,
            success_duration=duration if succeeded else 0.0,
        )
        statement = statement.on_conflict_do_update(
            index_elements=["bucket_time", "task_name", "model_name", "model_identifier", "provider_name"],
            set_={
                "request_count": ModelTaskUsage.request_count + 1,
                "failure_count": ModelTaskUsage.failure_count + statement.excluded.failure_count,
                "success_duration": ModelTaskUsage.success_duration + statement.excluded.success_duration,
            },
        )
        await session.execute(statement)
        await session.execute(
            delete(ModelTaskUsage).where(ModelTaskUsage.bucket_time < bucket - timedelta(days=RETENTION_DAYS))
        )

    @with_session
    async def aggregate(self, session: AsyncSession, hours: int) -> list[dict[str, Any]]:
        """按任务、模型与 Provider 聚合近 N 小时。"""
        end = to_naive_utc(now())
        start = (end - timedelta(hours=hours)).replace(second=0, microsecond=0)
        rows = await session.execute(
            select(
                ModelTaskUsage.task_name,
                ModelTaskUsage.model_name,
                ModelTaskUsage.provider_name,
                func.sum(ModelTaskUsage.request_count),
                func.sum(ModelTaskUsage.failure_count),
                func.sum(ModelTaskUsage.success_duration),
            )
            .where(ModelTaskUsage.bucket_time >= start, ModelTaskUsage.bucket_time <= end)
            .group_by(ModelTaskUsage.task_name, ModelTaskUsage.model_name, ModelTaskUsage.provider_name)
            .order_by(func.sum(ModelTaskUsage.request_count).desc())
        )
        return [
            {
                "task_name": str(task),
                "model_name": str(model),
                "provider_name": str(provider),
                "request_count": int(count or 0),
                "failure_count": int(failures or 0),
                "success_duration": float(duration or 0.0),
            }
            for task, model, provider, count, failures, duration in rows.all()
        ]


usage_repository = _UsageRepository()


def _provider_label(provider: Any) -> tuple[str, str]:
    """取实际调用的模型名与 Provider 名，异常时退回空串。"""
    try:
        model = str(provider.get_model() or "")
    except Exception:
        model = ""
    return model, str(getattr(provider, "provider_id", "") or "")


@asynccontextmanager
async def track_model_task_usage(task: str, provider: Any) -> AsyncIterator[None]:
    """只包裹实际调用；耗时不包含重试等待，超时取消也计入失败尝试。"""
    started = perf_counter()
    succeeded = False
    try:
        yield
        succeeded = True
    finally:
        duration = max(0.0, perf_counter() - started)
        model, provider_name = _provider_label(provider)
        task_name = task or "chat"
        try:
            # shield 保证硬超时取消后仍能落下统计
            await asyncio.shield(
                usage_repository.record(
                    task_name,
                    model or provider_name,
                    model,
                    provider_name,
                    duration,
                    succeeded,
                )
            )
        except Exception:
            logger.opt(exception=True).warning("[ModelUsage] 记录模型调用统计失败")


async def get_model_task_usage(hours: int = 24) -> dict[str, Any]:
    """返回按任务分组的调用次数、失败率与成功平均耗时。"""
    window = max(1, min(int(hours), RETENTION_DAYS * 24))
    rows = await usage_repository.aggregate(window)
    tasks: dict[str, dict[str, Any]] = {}
    for row in rows:
        task = tasks.setdefault(
            row["task_name"],
            {"task_name": row["task_name"], "request_count": 0, "failure_count": 0, "duration": 0.0, "models": []},
        )
        task["request_count"] += row["request_count"]
        task["failure_count"] += row["failure_count"]
        task["duration"] += row["success_duration"]
        successes = row["request_count"] - row["failure_count"]
        task["models"].append(
            {
                "model_name": row["model_name"],
                "provider_name": row["provider_name"],
                "request_count": row["request_count"],
                "failure_count": row["failure_count"],
                "avg_duration": row["success_duration"] / successes if successes else None,
                "failure_rate": row["failure_count"] / row["request_count"] if row["request_count"] else 0.0,
            }
        )
    items = []
    for task in tasks.values():
        successes = task["request_count"] - task["failure_count"]
        items.append(
            {
                "task_name": task["task_name"],
                "request_count": task["request_count"],
                "failure_count": task["failure_count"],
                "avg_duration": task["duration"] / successes if successes else None,
                "failure_rate": task["failure_count"] / task["request_count"] if task["request_count"] else 0.0,
                "models": task["models"][:_MAX_MODELS],
            }
        )
    items.sort(key=lambda item: item["request_count"], reverse=True)
    return {
        "hours": window,
        "request_count": sum(item["request_count"] for item in items),
        "tasks": items,
    }
