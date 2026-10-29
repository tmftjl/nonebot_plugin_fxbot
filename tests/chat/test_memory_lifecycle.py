"""对话存储生命周期：trace 清理、表达衰减与调度容错。"""

from __future__ import annotations

import unittest
from uuid import uuid4
from datetime import timedelta
from unittest.mock import AsyncMock, patch

from src.plugins.nonebot_plugin_fxbot.db import base_models
from src.plugins.nonebot_plugin_fxbot.chat.memory import state as memory_state, cleanup, migrate
from src.plugins.nonebot_plugin_fxbot.chat.memory.models import ChatMessage, ChatExpression, utc_now_naive
from src.plugins.nonebot_plugin_fxbot.chat.memory.repository import chat_repository


async def _prepare_db() -> None:
    await base_models.init_database()
    await migrate.ensure_schema()
    memory_state.mark_ready()


class LifecycleDbTests(unittest.IsolatedAsyncioTestCase):
    """清理与衰减在真实临时库上的行为。"""

    async def asyncSetUp(self) -> None:
        await _prepare_db()
        self.tenant = f"test:{uuid4().hex}"
        self.now = utc_now_naive()

    def _message(self, days: int) -> ChatMessage:
        return ChatMessage(
            tenant=self.tenant,
            content="旧消息",
            dedupe_key=uuid4().hex,
            occurred_at=self.now - timedelta(days=days),
        )

    def _expression(self, count: float, days: int) -> ChatExpression:
        token = uuid4().hex
        return ChatExpression(
            tenant=self.tenant,
            situation="打招呼",
            situation_hash=f"s:{token}",
            style="嗨",
            style_hash=f"y:{token}",
            count=count,
            checked=True,
            last_used_at=self.now - timedelta(days=days),
            updated_at=self.now - timedelta(days=days),
        )

    async def test_decay_expressions_rejects_idle_low_count(self) -> None:
        kept = self._expression(0.5, days=40)
        rejected = self._expression(0.05, days=40)
        recent = self._expression(0.05, days=0)
        async with base_models.async_maker() as session:  # type: ignore[misc]
            session.add_all([kept, rejected, recent])
            await session.commit()

        counts = await chat_repository.decay_expressions(self.now, idle_days=30)

        self.assertGreaterEqual(counts["decayed"], 3)
        self.assertGreaterEqual(counts["rejected"], 1)
        rows = {row.situation_hash: row for row in await ChatExpression.select_rows(tenant=self.tenant)}
        self.assertAlmostEqual(rows[kept.situation_hash].count, 0.495)
        self.assertTrue(rows[rejected.situation_hash].rejected)
        self.assertFalse(rows[recent.situation_hash].rejected)


class LifecycleTickTests(unittest.IsolatedAsyncioTestCase):
    """调度入口只清理 trace，失败不抛出。"""

    async def test_lifecycle_tick_swallows_trace_failure(self) -> None:
        with (
            patch.object(cleanup, "is_ready", return_value=True),
            patch.object(cleanup.chat_repository, "clear_traces", new=AsyncMock(side_effect=RuntimeError("db"))),
        ):
            result = await cleanup.lifecycle_tick()

        self.assertEqual(result, {})

    async def test_expression_decay_tick_swallows_failure(self) -> None:
        with (
            patch.object(cleanup, "is_ready", return_value=True),
            patch.object(cleanup.chat_repository, "decay_expressions", new=AsyncMock(side_effect=RuntimeError("db"))),
        ):
            self.assertEqual(await cleanup.expression_decay_tick(), {})
