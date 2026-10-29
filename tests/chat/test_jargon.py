"""独立黑话学习的阈值、证据、幂等与租户隔离回归。"""

from __future__ import annotations

import json
import unittest
from uuid import uuid4
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.plugins.nonebot_plugin_fxbot.db import base_models
from src.plugins.nonebot_plugin_fxbot.chat.memory import state, jargon, migrate
from src.plugins.nonebot_plugin_fxbot.chat.providers import LLMResponse
from src.plugins.nonebot_plugin_fxbot.chat.memory.models import ChatMessage
from src.plugins.nonebot_plugin_fxbot.chat.memory.repository import chat_repository


class JargonTests(unittest.IsolatedAsyncioTestCase):
    """使用临时测试数据库验证独立候选学习和释义。"""

    async def asyncSetUp(self) -> None:
        await base_models.init_database()
        await migrate.ensure_schema()
        state.mark_ready()
        self.tenant = f"onebot11:test:group:{uuid4().hex}"
        self.addAsyncCleanup(chat_repository.clear_tenant, self.tenant)
        self.enterContext(patch.object(jargon, "jargon_config", return_value={"learning_list": []}))
        self.provider = SimpleNamespace(chat_json=AsyncMock(return_value={"jargon": [{"term": "绝绝子"}]}))

        async def chat(messages, **kwargs):
            payload = await self.provider.chat_json(messages)
            if isinstance(payload, dict) and "jargon" in payload:
                payload = [{"content": item["term"], "id": "1"} for item in payload["jargon"]]
            return LLMResponse(content=json.dumps(payload, ensure_ascii=False))

        self.provider.chat = AsyncMock(side_effect=chat)

    async def seed(self, count: int) -> None:
        """写入带有同一个候选词的真实人类消息。"""
        for index in range(count):
            await chat_repository.append_message(
                ChatMessage(
                    tenant=self.tenant,
                    dedupe_key=uuid4().hex,
                    content=f"这次做的东西真是绝绝子，大家都觉得很好{index}",
                    sender_id="user",
                )
            )

    async def test_ten_messages_create_pending_candidate_and_do_not_repeat(self) -> None:
        await self.seed(9)
        self.assertFalse(await jargon.learn_jargon(self.tenant, provider=self.provider))
        self.provider.chat_json.assert_not_awaited()
        await self.seed(1)
        self.assertTrue(await jargon.learn_jargon(self.tenant, provider=self.provider))
        [row] = await chat_repository.list_jargon(self.tenant)
        self.assertEqual((row.term, row.count, row.status), ("绝绝子", 1, "pending"))
        self.assertFalse(await jargon.learn_jargon(self.tenant, provider=self.provider))
        self.assertEqual(await chat_repository.list_jargon("other-tenant"), [])

    async def test_four_occurrences_start_inference(self) -> None:
        for _ in range(3):
            await self.seed(10)
            self.assertTrue(await jargon.learn_jargon(self.tenant, provider=self.provider))
        self.provider.chat_json.side_effect = [
            {"jargon": [{"term": "绝绝子"}]},
            {"meaning": "形容特别好", "no_info": False},
            {"meaning": "一种普通口头禅"},
            {"is_similar": False},
        ]
        await self.seed(10)
        self.assertTrue(await jargon.learn_jargon(self.tenant, provider=self.provider))
        [row] = await chat_repository.list_jargon(self.tenant)
        self.assertEqual((row.count, row.last_inference_count, row.status), (4, 4, "active"))
        self.assertEqual(row.meaning, "形容特别好")

    def test_thresholds_do_not_repeat(self) -> None:
        for count in (4, 8, 25, 100):
            self.assertTrue(jargon.inference_due(count, count - 1))
            self.assertFalse(jargon.inference_due(count, count))

    async def test_disabled_jargon_keeps_cursor_and_candidates_untouched(self) -> None:
        await self.seed(10)
        target = self.tenant.rsplit(":", 1)[-1]
        with patch.object(
            jargon,
            "jargon_config",
            return_value={
                "learning_list": [{"platform": "onebot11", "item_id": target, "type": "group", "learn": False}]
            },
        ):
            self.assertFalse(await jargon.learn_jargon(self.tenant, provider=self.provider))
        self.assertEqual(await chat_repository.list_jargon(self.tenant), [])
        self.assertIsNone(await chat_repository.get_cursor(self.tenant, "jargon"))
