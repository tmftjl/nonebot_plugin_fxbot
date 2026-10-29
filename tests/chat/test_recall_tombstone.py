"""先撤回后落库的墓碑消费与原始消息持久化回归测试。"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from src.plugins.nonebot_plugin_fxbot.db import base_models
from src.plugins.nonebot_plugin_fxbot.chat.types import ChatRequest
from src.plugins.nonebot_plugin_fxbot.chat.memory import state, migrate
from src.plugins.nonebot_plugin_fxbot.chat.memory.models import ChatMessage, ChatRecallTombstone
from src.plugins.nonebot_plugin_fxbot.chat.message_receive import history
from src.plugins.nonebot_plugin_fxbot.chat.behavior.debounce import PendingBatch
from src.plugins.nonebot_plugin_fxbot.chat.memory.repository import chat_repository


class RecallTombstoneTests(unittest.IsolatedAsyncioTestCase):
    """独立临时 SQLite 验证撤回与各条持久化路径。"""

    async def asyncSetUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "recall.db"
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
        self.addAsyncCleanup(self.engine.dispose)
        self.maker = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)
        await migrate.ensure_schema(self.engine)
        for replacement in (
            patch.object(base_models, "_db_initialized", True),
            patch.object(base_models, "async_maker", self.maker),
            patch.object(state, "is_ready", return_value=True),
            patch.object(history, "_track_message", new=AsyncMock()),
        ):
            replacement.start()
            self.addCleanup(replacement.stop)

    def _message(self, message_id: str, *, tenant: str = "tenant") -> ChatMessage:
        """构造带稳定平台标识的原始消息。"""
        return ChatMessage(
            tenant=tenant,
            platform_message_id=message_id,
            dedupe_key=f"msg:{message_id}",
            content=f"原文 {message_id}",
        )

    def _request(self, message_id: str) -> ChatRequest:
        """构造走真实历史持久化入口的请求。"""
        return ChatRequest(
            session_id="tenant",
            user_id="user",
            group_id="group",
            text=f"原文 {message_id}",
            turn_id=f"turn:{message_id}",
            dedupe_key=f"msg:{message_id}",
            message_id=message_id,
            trigger_kind="mention",
        )

    async def _assert_recalled(self, message_id: str, *, operator: str = "admin") -> None:
        """检查原文不可读取且墓碑已消费。"""
        rows = await ChatMessage.select_rows(tenant="tenant", platform_message_id=message_id)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0].deleted)
        self.assertIsNotNone(rows[0].deleted_at)
        self.assertEqual(rows[0].delete_operator, operator)
        self.assertEqual(await ChatRecallTombstone.select_rows(tenant="tenant", platform_message_id=message_id), [])
        recent = await chat_repository.get_recent_messages("tenant", 100)
        learning = await chat_repository.messages_after("tenant", 0)
        self.assertFalse(any(message_id in str(item["content"]) for item in recent))
        self.assertNotIn(message_id, [row.platform_message_id for row in learning])

    async def test_append_message_both_orders_and_duplicate_delivery(self) -> None:
        for recall_first in (True, False):
            with self.subTest(recall_first=recall_first):
                message_id = f"append-{recall_first}"
                if recall_first:
                    result = await chat_repository.invalidate_message("tenant", message_id, operator="admin")
                    self.assertIsNone(result)
                    tombstones = await ChatRecallTombstone.select_rows(tenant="tenant", platform_message_id=message_id)
                    self.assertEqual(len(tombstones), 1)
                    await chat_repository.invalidate_message("tenant", message_id, operator="duplicate")
                    self.assertEqual(
                        len(await ChatRecallTombstone.select_rows(tenant="tenant", platform_message_id=message_id)), 1
                    )
                stored_id = await chat_repository.append_message(self._message(message_id))
                if not recall_first:
                    self.assertIsNotNone(
                        await chat_repository.invalidate_message("tenant", message_id, operator="admin")
                    )
                await self._assert_recalled(message_id)
                self.assertEqual(await chat_repository.append_message(self._message(message_id)), stored_id)
                await chat_repository.update_message_content("tenant", stored_id, "晚到的图片描述")
                await self._assert_recalled(message_id)
                row = (await ChatMessage.select_rows(tenant="tenant", id=stored_id))[0]
                self.assertEqual(row.content, f"原文 {message_id}")
                if recall_first:
                    self.assertEqual(row.deleted_at, tombstones[0].deleted_at)

    async def test_persist_turn_start_both_orders(self) -> None:
        for recall_first in (True, False):
            with self.subTest(recall_first=recall_first):
                message_id = f"turn-{recall_first}"
                request = self._request(message_id)
                if recall_first:
                    await chat_repository.invalidate_message("tenant", message_id, operator="admin")
                self.assertIsNotNone(await history.persist_turn_start(request, request.text, 0))
                if not recall_first:
                    await chat_repository.invalidate_message("tenant", message_id, operator="admin")
                await self._assert_recalled(message_id)

    async def test_merged_batch_checks_each_original_message_in_both_orders(self) -> None:
        for recall_first in (True, False):
            for recalled_index in range(3):
                with self.subTest(recall_first=recall_first, recalled_index=recalled_index):
                    requests = [self._request(f"batch-{recall_first}-{recalled_index}-{index}") for index in range(3)]
                    target = requests[recalled_index].message_id
                    batch = PendingBatch(tenant="tenant", owner_id="user", requests=requests, started=0.0, deadline=0.0)
                    merged = batch.merge()
                    if recall_first:
                        await chat_repository.invalidate_message("tenant", target, operator="admin")
                    await history.persist_batch(batch.requests)
                    self.assertIsNotNone(await history.persist_turn_start(merged, merged.text, 0))
                    if not recall_first:
                        await chat_repository.invalidate_message("tenant", target, operator="admin")
                    await self._assert_recalled(target)
                    for request in requests:
                        rows = await ChatMessage.select_rows(tenant="tenant", platform_message_id=request.message_id)
                        self.assertEqual(len(rows), 1)
                        self.assertEqual(rows[0].deleted, request.message_id == target)

    async def test_tombstone_is_tenant_scoped_and_survives_schema_recheck(self) -> None:
        await chat_repository.invalidate_message("tenant", "shared", operator="admin")
        await migrate.ensure_schema(self.engine)
        await chat_repository.append_message(self._message("shared", tenant="other"))
        other = await ChatMessage.select_rows(tenant="other", platform_message_id="shared")
        self.assertFalse(other[0].deleted)
        self.assertEqual(len(await ChatRecallTombstone.select_rows(tenant="tenant")), 1)
        await chat_repository.append_message(self._message("shared"))
        await self._assert_recalled("shared")

    async def test_rollback_keeps_tombstone_until_message_commits(self) -> None:
        await chat_repository.invalidate_message("tenant", "rollback", operator="admin")
        async with self.maker() as session:
            await chat_repository.append_message(self._message("rollback"), session=session)
            self.assertEqual(await ChatRecallTombstone.select_rows(tenant="tenant", session=session), [])
            await session.rollback()
        self.assertEqual(await ChatMessage.select_rows(tenant="tenant"), [])
        self.assertEqual(len(await ChatRecallTombstone.select_rows(tenant="tenant")), 1)
        await chat_repository.append_message(self._message("rollback"))
        await self._assert_recalled("rollback")

    async def test_concurrent_append_and_recall(self) -> None:
        for index in range(6):
            message_id = f"concurrent-{index}"
            operations = [
                chat_repository.invalidate_message("tenant", message_id, operator="admin"),
                chat_repository.append_message(self._message(message_id)),
            ]
            await asyncio.gather(*(operations if index % 2 else reversed(operations)))
            await self._assert_recalled(message_id)
