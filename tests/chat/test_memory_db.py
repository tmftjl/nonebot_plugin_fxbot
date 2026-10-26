"""记忆表结构迁移、降级状态和 UTC 存储测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch

from sqlalchemy import text, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from src.plugins.nonebot_plugin_fxbot.db import base_models
from src.plugins.nonebot_plugin_fxbot.utils.tz import as_shanghai, as_shanghai_local
from src.plugins.nonebot_plugin_fxbot.chat.memory import state, migrate
from src.plugins.nonebot_plugin_fxbot.chat.memory.models import ChatTurn, ChatMessage, ChatTurnItem, ChatSessionState

_ALL_MODELS = (ChatMessage, ChatTurn, ChatTurnItem, ChatSessionState)
_EXPECTED_INDEXES = {index.name for model in _ALL_MODELS for index in model.__table__.indexes}


class MigrationTests(unittest.IsolatedAsyncioTestCase):
    """临时 SQLite 上的建表与补列测试。"""

    async def asyncSetUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tempdir.cleanup)
        path = Path(self._tempdir.name) / "chat.db"
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
        self.addAsyncCleanup(self.engine.dispose)
        self.maker = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)

    async def _query(self, sql: str) -> list[tuple]:
        async with self.engine.connect() as conn:
            return list((await conn.execute(text(sql))).all())

    async def _table_names(self) -> set[str]:
        rows = await self._query("SELECT name FROM sqlite_master WHERE type = 'table'")
        return {row[0] for row in rows}

    async def _column_names(self, table: str) -> list[str]:
        rows = await self._query(f'PRAGMA table_info("{table}")')
        return [row[1] for row in rows]

    async def _index_names(self) -> set[str]:
        rows = await self._query("SELECT name FROM sqlite_master WHERE type = 'index'")
        return {row[0] for row in rows}

    async def test_fresh_database_matches_models(self) -> None:
        await migrate.ensure_schema(self.engine)
        table_names = await self._table_names()
        for model in _ALL_MODELS:
            with self.subTest(table=model.__tablename__):
                self.assertIn(model.__tablename__, table_names)
                self.assertEqual(await self._column_names(model.__tablename__), list(model.__table__.columns.keys()))
        self.assertLessEqual(_EXPECTED_INDEXES, await self._index_names())

    async def test_repeated_migration_is_idempotent(self) -> None:
        await migrate.ensure_schema(self.engine)
        await migrate.ensure_schema(self.engine)
        rows = await self._query("SELECT name, count(*) FROM sqlite_master WHERE type = 'table' GROUP BY name")
        counts = dict(rows)
        for model in _ALL_MODELS:
            self.assertEqual(counts[model.__tablename__], 1)
        rows = await self._query("SELECT name, count(*) FROM sqlite_master WHERE type = 'index' GROUP BY name")
        self.assertTrue(all(count == 1 for _, count in rows))

    async def test_missing_column_is_added(self) -> None:
        await migrate.ensure_schema(self.engine)
        async with self.engine.begin() as conn:
            await conn.execute(text("ALTER TABLE chat_message DROP COLUMN recall_note"))
        self.assertNotIn("recall_note", await self._column_names("chat_message"))

        await migrate.ensure_schema(self.engine)

        self.assertIn("recall_note", await self._column_names("chat_message"))
        async with self.maker() as session:
            session.add(ChatMessage(tenant="tenant", dedupe_key="msg:1"))
            await session.commit()

    async def test_unrepairable_table_raises(self) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(text("CREATE TABLE chat_session_state (id INTEGER PRIMARY KEY)"))
        with self.assertRaisesRegex(RuntimeError, r"chat_session_state\.tenant"):
            await migrate.ensure_schema(self.engine)

    async def test_missing_global_engine_raises(self) -> None:
        with patch.object(base_models, "engine", None):
            with self.assertRaisesRegex(RuntimeError, "数据库尚未初始化"):
                await migrate.ensure_schema()

    async def test_utc_round_trip_does_not_shift(self) -> None:
        await migrate.ensure_schema(self.engine)
        stored = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc).replace(tzinfo=None)
        async with self.maker() as session:
            session.add(ChatMessage(tenant="tenant", dedupe_key="msg:1", occurred_at=stored))
            await session.commit()

        async with self.maker() as session:
            loaded = (await session.execute(select(ChatMessage))).scalar_one()

        self.assertIsNone(loaded.occurred_at.tzinfo)
        self.assertEqual(loaded.occurred_at, stored)
        self.assertEqual(as_shanghai(loaded.occurred_at), datetime(2026, 1, 1, 8, tzinfo=timezone(timedelta(hours=8))))
        self.assertEqual(as_shanghai_local(loaded.occurred_at).hour, 0)


class DegradedStateTests(unittest.IsolatedAsyncioTestCase):
    """迁移失败只降级，不抛出。"""

    def setUp(self) -> None:
        state.mark_ready()
        self.addCleanup(state.mark_ready)

    async def test_prepare_marks_ready(self) -> None:
        with patch.object(migrate, "ensure_schema", new=AsyncMock()):
            await state.prepare_memory()
        self.assertTrue(state.is_ready())
        self.assertEqual(state.degraded_reason(), "")

    async def test_prepare_swallows_failure(self) -> None:
        with patch.object(migrate, "ensure_schema", new=AsyncMock(side_effect=RuntimeError("boom"))):
            await state.prepare_memory()
        self.assertFalse(state.is_ready())
        self.assertIn("RuntimeError: boom", state.degraded_reason())

    async def test_prepare_degrades_without_database(self) -> None:
        with patch.object(base_models, "engine", None):
            await state.prepare_memory()
        self.assertFalse(state.is_ready())
        self.assertIn("数据库尚未初始化", state.degraded_reason())
