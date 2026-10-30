"""对话存储生命周期：trace 清理、表达衰减与调度容错。"""

from __future__ import annotations

import tempfile
import unittest
from uuid import uuid4
from pathlib import Path
from datetime import timedelta
from unittest.mock import AsyncMock, patch

from sqlmodel import SQLModel, select
from sqlalchemy import func, insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from src.plugins.nonebot_plugin_fxbot.db import base_models
from src.plugins.nonebot_plugin_fxbot.chat.memory import state as memory_state, cleanup, migrate
from src.plugins.nonebot_plugin_fxbot.chat.memory.models import (
    ChatTrace,
    ChatMessage,
    ChatExpression,
    ChatPlannerSample,
    utc_now_naive,
)
from src.plugins.nonebot_plugin_fxbot.chat.memory.repository import DIAGNOSTIC_MAX_RECORDS, chat_repository


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


class DiagnosticRetentionTests(unittest.IsolatedAsyncioTestCase):
    """独立数据库验证诊断保留边界、全局上限及业务数据隔离。"""

    async def asyncSetUp(self) -> None:
        await base_models.init_database()
        folder = tempfile.TemporaryDirectory(prefix="fxbot-diagnostic-retention-")
        self.addCleanup(folder.cleanup)
        self.engine = create_async_engine("sqlite+aiosqlite:///" + (Path(folder.name) / "test.db").as_posix())
        self.addAsyncCleanup(self.engine.dispose)
        async with self.engine.begin() as connection:
            await connection.run_sync(
                lambda database: SQLModel.metadata.create_all(
                    database,
                    tables=[model.__table__ for model in (ChatTrace, ChatPlannerSample, ChatMessage, ChatExpression)],
                )
            )
        self.maker = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)
        self.now = utc_now_naive()

    async def test_72_hour_boundary_preserves_chat_and_learning(self) -> None:
        cutoff = self.now - timedelta(hours=72)
        async with self.maker() as session:
            for model in (ChatTrace, ChatPlannerSample):
                for index, timestamp in enumerate((cutoff - timedelta(microseconds=1), cutoff, self.now)):
                    session.add(model(tenant=f"scope-{index}", turn_id=f"turn-{index}", created_at=timestamp))
            session.add(ChatMessage(tenant="chat", dedupe_key="message", content="保留聊天", occurred_at=cutoff))
            session.add(
                ChatExpression(
                    tenant="chat", situation="测试场景", situation_hash="s", style_hash="t", style="保留表达"
                )
            )
            self.assertEqual(await chat_repository.clear_traces(cutoff, session=session), 1)
            self.assertEqual(await chat_repository.clear_planner_samples(cutoff, session=session), 1)
            for model in (ChatTrace, ChatPlannerSample):
                rows = list((await session.execute(select(model).order_by(model.id))).scalars())
                self.assertEqual([row.created_at for row in rows], [cutoff, self.now])
            for model in (ChatMessage, ChatExpression):
                self.assertEqual((await session.execute(select(func.count()).select_from(model))).scalar_one(), 1)

    async def test_cleanup_caps_each_table_at_10000_across_sessions(self) -> None:
        cutoff = self.now - timedelta(hours=72)
        async with self.maker() as session:
            for model in (ChatTrace, ChatPlannerSample):
                template = model(tenant="", turn_id="").model_dump(exclude={"id"})
                rows = [
                    {**template, "tenant": f"scope-{index % 2}", "turn_id": str(index), "created_at": self.now}
                    for index in range(DIAGNOSTIC_MAX_RECORDS + 1)
                ]
                await session.execute(insert(model), rows)
            self.assertEqual(await chat_repository.clear_traces(cutoff, session=session), 1)
            self.assertEqual(await chat_repository.clear_planner_samples(cutoff, session=session), 1)
            for model in (ChatTrace, ChatPlannerSample):
                count = (await session.execute(select(func.count()).select_from(model))).scalar_one()
                self.assertEqual(count, 10000)
                self.assertIsNone((await session.execute(select(model).where(model.turn_id == "0"))).scalars().first())
                self.assertIsNotNone(
                    (await session.execute(select(model).where(model.turn_id == "10000"))).scalars().first()
                )


class LifecycleTickTests(unittest.IsolatedAsyncioTestCase):
    """规划样本和追踪清理单项失败不阻断另一项。"""

    async def test_lifecycle_tick_swallows_trace_failure(self) -> None:
        with (
            patch.object(cleanup, "is_ready", return_value=True),
            patch.object(cleanup.chat_repository, "clear_traces", new=AsyncMock(side_effect=RuntimeError("db"))),
            patch.object(cleanup.chat_repository, "clear_planner_samples", new=AsyncMock(return_value=3)) as samples,
        ):
            result = await cleanup.lifecycle_tick()

        self.assertEqual(result, {"planner_samples": 3})
        samples.assert_awaited_once()

    async def test_planner_cleanup_failure_keeps_successful_trace_cleanup(self) -> None:
        moment = utc_now_naive()
        with (
            patch.object(cleanup, "is_ready", return_value=True),
            patch.object(cleanup.chat_repository, "clear_traces", new=AsyncMock(return_value=2)) as traces,
            patch.object(
                cleanup.chat_repository, "clear_planner_samples", new=AsyncMock(side_effect=RuntimeError("db"))
            ),
        ):
            self.assertEqual(await cleanup.lifecycle_tick(now=moment), {"traces": 2})
        traces.assert_awaited_once_with(moment - timedelta(hours=72))

    async def test_expression_decay_tick_swallows_failure(self) -> None:
        with (
            patch.object(cleanup, "is_ready", return_value=True),
            patch.object(cleanup.chat_repository, "decay_expressions", new=AsyncMock(side_effect=RuntimeError("db"))),
        ):
            self.assertEqual(await cleanup.expression_decay_tick(), {})
