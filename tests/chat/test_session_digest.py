"""控制台会话摘要与最近对话只读接口。"""

from __future__ import annotations

import unittest
from uuid import uuid4

from src.plugins.nonebot_plugin_fxbot.db import base_models
from src.plugins.nonebot_plugin_fxbot.chat.memory import migrate
from src.plugins.nonebot_plugin_fxbot.console.routes import chat as chat_routes
from src.plugins.nonebot_plugin_fxbot.chat.memory.models import (
    ChatTurn,
    ChatEmoji,
    ChatStyle,
    ChatJargon,
    ChatMessage,
    ChatJobCursor,
    ChatExpression,
    ChatSessionState,
    utc_now_naive,
)
from src.plugins.nonebot_plugin_fxbot.chat.memory.repository import chat_repository


class SessionDigestTests(unittest.IsolatedAsyncioTestCase):
    """摘要汇总学习资产、轮次与学习游标，消息接口剥离内部字段。"""

    async def asyncSetUp(self) -> None:
        await base_models.init_database()
        await migrate.ensure_schema()
        self.tenant = f"test:{uuid4().hex}"
        now = utc_now_naive()
        assert base_models.async_maker is not None
        async with base_models.async_maker() as session:
            session.add(ChatSessionState(tenant=self.tenant, generation=2, cleared_at=now, updated_at=now))
            session.add(
                ChatStyle(
                    tenant=self.tenant,
                    version=3,
                    summary="短句",
                    rules='["a", "b"]',
                    examples='[{"text": "x"}]',
                    learn_count=5,
                    use_count=2,
                )
            )
            session.add(
                ChatJargon(
                    tenant=self.tenant,
                    term="yyds",
                    normalized="yyds",
                    meaning="永远的神",
                    status="active",
                )
            )
            session.add(
                ChatJargon(
                    tenant=self.tenant,
                    term="绝",
                    normalized="绝",
                    meaning="好",
                    status="pending",
                )
            )
            session.add(
                ChatExpression(
                    tenant=self.tenant,
                    situation="夸人",
                    situation_hash="s1",
                    style="直白",
                    style_hash="x1",
                    checked=True,
                )
            )
            session.add(
                ChatExpression(
                    tenant=self.tenant,
                    situation="拒绝",
                    situation_hash="s2",
                    style="敷衍",
                    style_hash="x2",
                    rejected=True,
                )
            )
            session.add(ChatEmoji(tenant=self.tenant, hash="h1", path="p1"))
            session.add(ChatEmoji(tenant=self.tenant, hash="h2", path="p2", enabled=False))
            session.add(ChatTurn(tenant=self.tenant, turn_key="t1", status="sent"))
            session.add(ChatTurn(tenant=self.tenant, turn_key="t2", status="failed", error="x"))
            self.message_ids = []
            for index in range(3):
                message = ChatMessage(
                    tenant=self.tenant,
                    sender_id="u1",
                    sender_name="小明",
                    content="小明(u1): hi 0" if index == 0 else f"hi {index}",
                    dedupe_key=f"d{index}",
                    occurred_at=now,
                )
                session.add(message)
                await session.flush()
                self.message_ids.append(message.id)
                # 全局序号夹杂其他租户，不能直接相减当成本会话消息数。
                session.add(ChatMessage(tenant=self.tenant + ":other", content="other", dedupe_key=f"other{index}"))
                await session.flush()
            session.add(
                ChatJobCursor(
                    tenant=self.tenant,
                    job="expression",
                    cursor_seq=self.message_ids[0],
                    fail_count=1,
                    last_error="boom",
                    next_retry_at=now,
                )
            )
            session.add(ChatJobCursor(tenant=self.tenant, job="jargon", cursor_seq=self.message_ids[1]))
            session.add(ChatJobCursor(tenant=self.tenant, job="memory", cursor_seq=0))
            session.add(ChatMessage(tenant=self.tenant, content="deleted", deleted=True, dedupe_key="deleted"))
            await session.commit()

    async def test_digest_summarizes_assets_and_cursor(self) -> None:
        digest = await chat_repository.session_digest(self.tenant)

        state = digest["state"]
        assert isinstance(state, dict)
        self.assertEqual(state["generation"], 2)
        self.assertIsNotNone(state["last_active_at"])
        style = digest["style"]
        assert isinstance(style, dict)
        self.assertEqual((style["version"], style["rules"], style["examples"]), (3, 2, 1))
        self.assertEqual(digest["jargon"], {"active": 1, "pending": 1})
        self.assertEqual(digest["expressions"], {"total": 2, "checked": 1, "rejected": 1})
        self.assertEqual(digest["emoji"], {"total": 2, "enabled": 1})
        self.assertEqual(digest["turns"], {"sent": 1, "failed": 1})
        self.assertEqual(set(digest["cursors"]), {"expression", "jargon"})
        cursor = digest["cursors"]["expression"]
        assert isinstance(cursor, dict)
        self.assertEqual(cursor["cursor_seq"], self.message_ids[0])
        self.assertEqual(cursor["fail_count"], 1)
        self.assertEqual(cursor["last_error"], "boom")
        self.assertEqual(cursor["backlog"], 2)
        self.assertEqual(digest["cursors"]["jargon"]["backlog"], 1)

    async def test_digest_route_formats_shanghai_time(self) -> None:
        result = await chat_routes.get_session_digest(self.tenant)

        self.assertTrue(result["digest"]["state"]["cleared_at"].endswith("+08:00"))
        self.assertTrue(result["digest"]["state"]["last_active_at"].endswith("+08:00"))
        self.assertTrue(result["digest"]["cursors"]["expression"]["next_retry_at"].endswith("+08:00"))

    async def test_recent_messages_strip_internal_fields(self) -> None:
        result = await chat_routes.list_recent_messages(self.tenant, 10)

        self.assertEqual([item["role"] for item in result["items"]], ["user", "user", "user"])
        self.assertEqual(result["items"][0]["sender_name"], "小明")
        self.assertEqual(result["items"][0]["content"], "hi 0")
        self.assertTrue(result["items"][0]["timestamp"].endswith("+08:00"))
        self.assertNotIn("_sender_id", result["items"][0])


if __name__ == "__main__":
    unittest.main()
