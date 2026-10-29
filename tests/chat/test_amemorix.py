"""A_Memorix 宿主适配层：人物身份、消息写回与摘要文本。"""

from __future__ import annotations

import json
import unittest
from uuid import uuid4
from datetime import datetime, timezone

from sqlmodel import select
from src.plugins.nonebot_plugin_fxbot.db import base_models
from src.plugins.nonebot_plugin_fxbot.chat.memory import state as memory_state
from src.plugins.nonebot_plugin_fxbot.chat.memory.models import ChatTurn, ChatMessage, ChatTurnItem
from src.plugins.nonebot_plugin_fxbot.chat.amemorix.models import ChatPerson
from src.plugins.nonebot_plugin_fxbot.chat.amemorix.people import remember_person
from src.plugins.nonebot_plugin_fxbot.chat.message_receive import history as history_module
from src.plugins.nonebot_plugin_fxbot.chat.memory.repository import chat_repository
from src.plugins.nonebot_plugin_fxbot.chat.amemorix._host.person_db import (
    person_scope,
    make_person_id,
    resolve_person_identifier,
)
from src.plugins.nonebot_plugin_fxbot.chat.amemorix._host.message_service import (
    _strip_speaker_prefix,
    get_messages_by_time_in_chat,
)


class PersonIdentityTests(unittest.IsolatedAsyncioTestCase):
    """人物记录按租户隔离，person_name 不随后续昵称漂移。"""

    async def asyncSetUp(self) -> None:
        await base_models.init_database()
        await memory_state.prepare_memory()
        self.tenant = f"test:{uuid4().hex}:group:g1"

    async def test_remember_person_upsert_and_group_card(self) -> None:
        person_id = await remember_person(self.tenant, "test", "u1", "小明", group_id="g1", group_cardname="明哥")
        self.assertEqual(person_id, make_person_id(self.tenant, "test", "u1"))

        same_id = await remember_person(self.tenant, "test", "u1", "小明改名", group_id="g1", group_cardname="明明")
        self.assertEqual(same_id, person_id)

        async with base_models.async_maker() as session:
            rows = (await session.execute(select(ChatPerson).where(ChatPerson.tenant == self.tenant))).scalars().all()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].person_name, "小明")
        self.assertEqual(rows[0].user_nickname, "小明改名")
        cards = json.loads(rows[0].group_cardname)
        self.assertEqual(cards, [{"group_id": "g1", "group_cardname": "明明"}])

        with person_scope(self.tenant):
            self.assertEqual(resolve_person_identifier(person_id), person_id)
            self.assertEqual(resolve_person_identifier("小明"), person_id)
        with person_scope(f"other:{uuid4().hex}:group:g1"):
            self.assertEqual(resolve_person_identifier("小明"), "")

    async def test_track_message_records_sender_without_memory(self) -> None:
        """记忆内核未启用时仍登记人物，摘要任务由下游自行短路。"""
        await history_module._track_message(self.tenant, "test", "u2", "小红", "g1")

        async with base_models.async_maker() as session:
            row = (await session.execute(select(ChatPerson).where(ChatPerson.tenant == self.tenant))).scalars().first()
        self.assertIsNotNone(row)
        self.assertEqual(row.user_id, "u2")
        self.assertEqual(row.user_nickname, "小红")


class ChatBridgeTests(unittest.IsolatedAsyncioTestCase):
    """摘要读取同时覆盖用户原文与已发送的机器人回复。"""

    async def asyncSetUp(self) -> None:
        await base_models.init_database()
        await memory_state.prepare_memory()
        self.tenant = f"test:{uuid4().hex}:group:g1"
        self.timestamp = datetime(2026, 1, 1, 4, 0, tzinfo=timezone.utc).replace(tzinfo=None)

    async def test_window_contains_both_sides_without_speaker_prefix(self) -> None:
        await chat_repository.append_message(
            ChatMessage(
                tenant=self.tenant,
                kind="user",
                platform="test",
                sender_id="u1",
                sender_name="小明",
                content="小明(u1): 你好",
                dedupe_key=f"msg:{uuid4().hex}",
                occurred_at=self.timestamp,
            )
        )
        turn_id = await chat_repository.upsert_turn(
            ChatTurn(
                tenant=self.tenant,
                turn_key=f"turn:{uuid4().hex}",
                generation=0,
                trigger_message_ids="[]",
                author_ids='["u1"]',
                trigger_kind="mention",
                request_digest="digest",
                status="sent",
            )
        )
        await chat_repository.append_turn_item(
            ChatTurnItem(
                tenant=self.tenant,
                turn_id=turn_id,
                item_seq=1,
                role="assistant",
                content="在的",
                visible=True,
                send_status="sent",
                created_at=self.timestamp,
            )
        )

        start = datetime(2026, 1, 1, 3, 59, tzinfo=timezone.utc).timestamp()
        end = datetime(2026, 1, 1, 4, 1, tzinfo=timezone.utc).timestamp()
        messages = get_messages_by_time_in_chat(self.tenant, start, end)

        self.assertEqual([item.processed_plain_text for item in messages], ["你好", "在的"])
        self.assertTrue(messages[1].message_info.user_info.user_nickname)


class SpeakerPrefixTests(unittest.TestCase):
    """摘要文本会自己加发言人，读取时先剥掉落库前缀。"""

    def test_strip_nickname_and_user_id(self) -> None:
        self.assertEqual(_strip_speaker_prefix("小明(10001): 你好", "10001", "小明"), "你好")

    def test_strip_fallback_user_id(self) -> None:
        self.assertEqual(_strip_speaker_prefix("用户10001: 你好", "10001", ""), "你好")

    def test_keep_content_without_prefix(self) -> None:
        self.assertEqual(_strip_speaker_prefix("你好", "10001", "小明"), "你好")
