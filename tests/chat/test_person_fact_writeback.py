"""人物事实写回的回复目标、邻近上下文和会话隔离。"""

from __future__ import annotations

import unittest
from uuid import uuid4
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.plugins.nonebot_plugin_fxbot.db import base_models
from src.plugins.nonebot_plugin_fxbot.chat.memory import migrate
from src.plugins.nonebot_plugin_fxbot.chat.amemorix import writeback
from src.plugins.nonebot_plugin_fxbot.chat.memory.models import ChatMessage
from src.plugins.nonebot_plugin_fxbot.chat.memory.repository import chat_repository


class PersonFactEvidenceTests(unittest.IsolatedAsyncioTestCase):
    """用真实记录验证事实证据不会取错人或跨会话。"""

    async def asyncSetUp(self) -> None:
        await base_models.init_database()
        await migrate.ensure_schema()
        self.tenant = f"test:person-fact:{uuid4().hex}"
        self.addAsyncCleanup(chat_repository.clear_tenant, self.tenant)

    async def append(self, identifier: str, sender: str, content: str) -> None:
        """保存一条有平台消息身份的测试发言。"""
        await chat_repository.append_message(
            ChatMessage(
                tenant=self.tenant,
                dedupe_key=f"message:{identifier}",
                platform_message_id=identifier,
                sender_id=sender,
                sender_name=sender,
                content=content,
            )
        )

    async def test_reply_target_wins_over_newer_target_messages(self) -> None:
        await self.append("question", "other", "你对什么过敏？")
        await self.append("answer", "target", "青霉素")
        await self.append("newer", "target", "我喜欢打游戏")
        evidence, context = await writeback._collect_evidence(self.tenant, "target", "answer", None)
        self.assertEqual([row["message_id"] for row in evidence], ["answer"])
        self.assertTrue(any("过敏" in row["content"] for row in context))

    async def test_other_session_reply_target_cannot_supply_evidence(self) -> None:
        await self.append("answer", "target", "青霉素")
        evidence, context = await writeback._collect_evidence(self.tenant + ":other", "target", "answer", None)
        self.assertEqual((evidence, context), ([], []))

    async def test_wrong_author_target_falls_back_to_recent_six_messages(self) -> None:
        await self.append("old", "target", "我住在杭州")
        for index in range(6):
            await self.append(f"other-{index}", "other", "我住在广州")
        evidence, context = await writeback._collect_evidence(self.tenant, "target", "other-5", None)
        self.assertEqual(evidence, [])
        self.assertEqual(len(context), 6)

    async def test_model_fabricated_evidence_id_is_not_persisted(self) -> None:
        await self.append("answer", "target", "我住在杭州")
        bridge = writeback.ChatWriteback()
        bridge._fact_client = SimpleNamespace(
            generate_response=AsyncMock(
                return_value=SimpleNamespace(
                    response='[{"fact":"目标用户住在杭州","evidence_message_id":"fabricated"}]'
                )
            )
        )
        with (
            patch.object(writeback, "remember_person", AsyncMock()),
            patch.object(writeback, "ingest_text", AsyncMock()) as ingest,
        ):
            await bridge._handle_fact(
                {"tenant": self.tenant, "user_id": "target", "reply_text": "知道了", "reply_to_message_id": "answer"}
            )
        self.assertEqual(ingest.await_args.kwargs["metadata"]["evidence_message_ids"], ["answer"])
