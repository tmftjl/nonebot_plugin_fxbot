"""MaiBot 裁切素材学习入口的缓冲、失败保留与取消回归。"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from src.plugins.nonebot_plugin_fxbot.chat.memory import trimmed
from src.plugins.nonebot_plugin_fxbot.chat.memory.models import ChatMessage


class TrimmedLearningTests(unittest.IsolatedAsyncioTestCase):
    """学习只消费裁切的真实会话消息，不扫描未裁切消息。"""

    async def asyncSetUp(self) -> None:
        self.worker = trimmed.TrimmedLearning()
        self.addAsyncCleanup(self.worker.stop)
        self.rows = [
            ChatMessage(id=index + 1, tenant="tenant", dedupe_key=str(index), content="真实用户消息")
            for index in range(10)
        ]
        self.expression = self.enterContext(patch.object(trimmed, "learn_expressions", AsyncMock(return_value=True)))
        self.jargon = self.enterContext(patch.object(trimmed, "learn_jargon", AsyncMock(return_value=True)))
        self.enterContext(patch.object(trimmed, "expression_config", return_value={"max_expression_learner": 3}))
        self.source = self.enterContext(
            patch.object(trimmed.chat_repository, "learning_sources", AsyncMock(return_value=self.rows))
        )

    async def wait_idle(self) -> None:
        for _ in range(20):
            tasks = [
                *self.worker.tasks.values(),
                *(task for group in self.worker.ingest_tasks.values() for task in group),
            ]
            if not tasks:
                return
            await asyncio.gather(*tasks)
            await asyncio.sleep(0)
        self.fail("学习任务未结束")

    async def test_trimmed_user_sources_reach_both_learners(self) -> None:
        self.worker.collect(
            "tenant",
            [
                {"role": "user", "content": "原始消息", "_source_ids": ["message"]},
                {"role": "assistant", "content": "机器人回复", "_source_ids": ["self-message"]},
            ],
        )
        await self.wait_idle()
        self.assertEqual(self.source.await_args.args[1], ["message"])
        self.expression.assert_awaited_once_with("tenant", messages=self.rows)
        self.jargon.assert_awaited_once_with("tenant", source_messages=self.rows)

    async def test_failed_batch_is_retained_without_busy_retry(self) -> None:
        self.expression.return_value = False
        self.worker.collect("tenant", [{"role": "user", "content": "原始消息", "_source_ids": ["message"]}])
        await self.wait_idle()
        self.assertEqual(len(self.worker.pending[("tenant", "expression")]), 10)
        self.worker.schedule()
        await self.wait_idle()
        self.expression.assert_awaited_once()

    async def test_clear_invalidates_ingress_waiting_on_database(self) -> None:
        entered, released = asyncio.Event(), asyncio.Event()

        async def sources(*args):
            entered.set()
            await released.wait()
            return self.rows

        self.source.side_effect = sources
        self.worker.collect("tenant", [{"role": "user", "content": "原始消息", "_source_ids": ["message"]}])
        await entered.wait()
        tasks = list(self.worker.ingest_tasks["tenant"])
        self.worker.clear("tenant")
        released.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.assertEqual(self.worker.pending, {})
        self.expression.assert_not_awaited()
