"""移植 MaiBot 062d45c 的裁切素材缓冲和学习批次闸门。"""

from __future__ import annotations

import asyncio
from typing import Any
from collections import OrderedDict

from nonebot import logger

from .config import expression_config
from .jargon import learn_jargon
from .models import ChatMessage
from .expression import learn_expressions
from .repository import chat_repository

MAX_PENDING_MESSAGES = 100
MIN_MESSAGES = 10


class TrimmedLearning:
    """每个会话保留裁切的真实素材，表达与黑话各自消费同一份来源。"""

    def __init__(self) -> None:
        self.pending: dict[tuple[str, str], OrderedDict[int, ChatMessage]] = {}
        self.tasks: dict[tuple[str, str], asyncio.Task] = {}
        self.generations: dict[str, int] = {}
        self.ingest_tasks: dict[str, set[asyncio.Task]] = {}
        self.blocked: set[tuple[str, str]] = set()
        self.stopping = False

    def collect(self, tenant: str, messages: list[dict[str, Any]]) -> None:
        """只将裁切的真实消息投递到后台学习，不阻塞回复。"""
        if self.stopping:
            return
        identifiers = [
            str(value)
            for message in messages
            if message.get("role") == "user"
            for value in message.get("_source_ids", [])
        ]
        dedupe = [
            str(message["_dedupe_key"])
            for message in messages
            if message.get("role") == "user" and message.get("_dedupe_key")
        ]
        texts = [
            str(message.get("content") or "")
            for message in messages
            if message.get("role") == "user" and isinstance(message.get("content"), str)
        ]
        if not identifiers and not dedupe and not texts:
            return
        generation = self.generations.get(tenant, 0)

        async def ingest() -> None:
            try:
                rows = await chat_repository.learning_sources(tenant, identifiers, dedupe, texts)
                if self.generations.get(tenant, 0) != generation:
                    return
                for job in ("expression", "jargon"):
                    self.blocked.discard((tenant, job))
                    pending = self.pending.setdefault((tenant, job), OrderedDict())
                    for row in rows:
                        pending.setdefault(int(row.id or 0), row)
                        if len(pending) > MAX_PENDING_MESSAGES:
                            pending.popitem(last=False)
                self.schedule()
            except Exception:
                logger.opt(exception=True).warning("[ChatLearning] 裁切素材接入失败")

        task = asyncio.create_task(ingest())
        self.ingest_tasks.setdefault(tenant, set()).add(task)
        task.add_done_callback(lambda done: self.ingest_tasks.get(tenant, set()).discard(done))

    def schedule(self) -> None:
        """沿用 MaiBot 的每会话互斥与全局并发限制。"""
        if self.stopping:
            return
        maximum = int(expression_config().get("max_expression_learner", 3))
        if maximum <= 0:
            return
        for key, rows in list(self.pending.items()):
            tenant, job = key
            if len(rows) < MIN_MESSAGES or key in self.tasks or key in self.blocked:
                continue
            if sum(active_job == job for _, active_job in self.tasks) >= maximum:
                continue
            snapshot = list(rows.values())
            generation = self.generations.get(tenant, 0)

            async def run(key=key, snapshot=snapshot, generation=generation) -> None:
                tenant, job = key
                try:
                    saved = (
                        await learn_expressions(tenant, messages=snapshot)
                        if job == "expression"
                        else await learn_jargon(tenant, source_messages=snapshot)
                    )
                    if saved and self.generations.get(tenant, 0) == generation:
                        waiting = self.pending.get(key, {})
                        for message in snapshot:
                            waiting.pop(int(message.id or 0), None)
                    elif self.generations.get(tenant, 0) == generation:
                        self.blocked.add(key)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self.blocked.add(key)
                    logger.opt(exception=True).warning("[ChatLearning] 裁切素材学习失败，保留素材")

            task = asyncio.create_task(run())
            self.tasks[key] = task

            def finished(done, key=key) -> None:
                if self.tasks.get(key) is done:
                    self.tasks.pop(key, None)
                    self.schedule()

            task.add_done_callback(finished)

    def clear(self, tenant: str) -> None:
        """清空时取消学习并使尚未接入的裁切素材失效。"""
        self.generations[tenant] = self.generations.get(tenant, 0) + 1
        for key in [key for key in self.pending if key[0] == tenant]:
            self.pending.pop(key, None)
            self.blocked.discard(key)
        for key in [key for key in self.tasks if key[0] == tenant]:
            self.tasks.pop(key).cancel()
        for task in self.ingest_tasks.pop(tenant, set()):
            task.cancel()

    async def stop(self) -> None:
        """关闭前等所有学习任务取消，避免 Provider 释放后继续生成。"""
        self.stopping = True
        tasks = [*self.tasks.values(), *(task for group in self.ingest_tasks.values() for task in group)]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.tasks.clear()
        self.ingest_tasks.clear()
        self.pending.clear()
        self.blocked.clear()


trimmed_learning = TrimmedLearning()
