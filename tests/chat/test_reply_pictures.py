"""回复图片引用、工具媒体索引和成功发送历史。"""

from __future__ import annotations

import base64
import unittest
from uuid import uuid4
from types import SimpleNamespace
from dataclasses import replace
from unittest.mock import AsyncMock, patch

from src.plugins.nonebot_plugin_fxbot.db import base_models
from src.plugins.nonebot_plugin_fxbot.chat.types import ChatRequest, ChatResponse
from src.plugins.nonebot_plugin_fxbot.chat.memory import state, migrate
from src.plugins.nonebot_plugin_fxbot.chat.session import ChatSessionStore
from src.plugins.nonebot_plugin_fxbot.chat.heart_flow import attachments
from src.plugins.nonebot_plugin_fxbot.chat.memory.models import ChatTurn, ChatTurnItem
from src.plugins.nonebot_plugin_fxbot.chat.message_receive import sender, history


class ReplyPictureTests(unittest.IsolatedAsyncioTestCase):
    """附图只来自当前会话已经拥有的图片，发送失败不写可见记录。"""

    def setUp(self) -> None:
        self.data = b"synthetic-image-bytes"
        self.store = ChatSessionStore()
        self.service = SimpleNamespace(
            session_store=self.store, is_current=lambda *args: True, is_latest=lambda *args: True, mark_sent=AsyncMock()
        )
        self.request = ChatRequest(
            session_id="pictures-a",
            user_id="user",
            text="看图",
            message_id="m1",
            group_id=None,
            turn_id="pictures-turn",
            dedupe_key="pictures-m1",
            trigger_kind="direct",
        )
        self.enterContext(
            patch.object(attachments.chat_repository, "message_by_identity", AsyncMock(return_value=None))
        )

    async def test_context_picture_index_and_other_session_rejection(self) -> None:
        self.store.append(
            "pictures-a", {"role": "user", "_source_ids": ["old"], "_image_sources": [b"first", self.data]}
        )
        result = await attachments.resolve_pictures(self.service, self.request, [{"msg_id": "old", "index": 1}], [])
        self.assertEqual(result, [self.data])
        other = replace(self.request, session_id="pictures-b")
        with self.assertRaises(ValueError):
            await attachments.resolve_pictures(self.service, other, [{"msg_id": "old"}], [])

    async def test_tool_media_keeps_original_content_item_index(self) -> None:
        result = {
            "ok": True,
            "result": {
                "content_items": [
                    {"content_type": "text", "text": "结果"},
                    {
                        "content_type": "image",
                        "data": base64.b64encode(self.data).decode("ascii"),
                        "mime_type": "image/png",
                    },
                ]
            },
        }
        projected, trace = attachments.tool_media(result, "call-image")
        identifier = "tool_result:call-image:2"
        self.assertEqual(projected["result"]["media_indexes"], [identifier])
        self.assertNotIn("content_items", projected["result"])
        pictures = await attachments.resolve_pictures(self.service, self.request, [{"media_index": identifier}], trace)
        self.assertEqual(pictures, [self.data])
        with self.assertRaises(ValueError):
            await attachments.resolve_pictures(self.service, self.request, [{"media_index": identifier}], [])

    async def test_model_cannot_use_a_file_path_as_picture_reference(self) -> None:
        with self.assertRaises(ValueError):
            await attachments.resolve_pictures(self.service, self.request, [{"msg_id": "C:/Windows/win.ini"}], [])

    async def test_sent_picture_is_recorded_once_and_failure_is_invisible(self) -> None:
        response = ChatResponse(text="这是图片", metadata={"attach_pictures": [self.data]})
        matcher = SimpleNamespace(send=AsyncMock(return_value={"message_id": "sent-picture"}))
        runtime = SimpleNamespace(bot=object())
        with (
            patch.object(sender, "build_message_segment", side_effect=lambda bot, kind, value: (kind, value)),
            patch.object(sender, "build_message", side_effect=lambda bot, *segments: list(segments)),
        ):
            await sender.send_pictures(self.service, self.request, response, runtime, matcher)
            await sender.send_pictures(self.service, self.request, response, runtime, matcher)
            self.assertEqual(matcher.send.await_count, 1)
            self.service.mark_sent.assert_awaited_once()
            self.assertEqual(self.service.mark_sent.await_args.args[3], "sent-picture")
            self.service.mark_sent.reset_mock()
            matcher.send.side_effect = RuntimeError("synthetic failure")
            await sender.send_pictures(
                self.service,
                self.request,
                ChatResponse(text="图", metadata={"attach_pictures": [self.data]}),
                runtime,
                matcher,
            )
            self.service.mark_sent.assert_not_awaited()

    async def test_cleared_turn_cannot_send_pictures(self) -> None:
        self.service.is_current = lambda *args: False
        matcher = SimpleNamespace(send=AsyncMock())
        await sender.send_pictures(
            self.service,
            self.request,
            ChatResponse(text="图", metadata={"attach_pictures": [self.data]}),
            SimpleNamespace(bot=object()),
            matcher,
        )
        matcher.send.assert_not_awaited()

    async def test_picture_receipt_persists_as_visible_history(self) -> None:
        await base_models.init_database()
        await migrate.ensure_schema()
        state.mark_ready()
        tenant = f"test:picture-receipt:{uuid4().hex}"
        self.addAsyncCleanup(attachments.chat_repository.clear_tenant, tenant)
        request = replace(self.request, session_id=tenant, accepted_epoch=0)
        request.metadata["sent_quote_id"] = "previous-text-quote"
        self.store.append(tenant, {"role": "user", "content": "看图"})
        turn_id = await attachments.chat_repository.upsert_turn(ChatTurn(tenant=tenant, turn_key=request.turn_id))

        async def mark_sent(request, response, index, receipt, *, content):
            await history.mark_sent(self.service, request, response, index, receipt, content=content, limit=30)

        self.service.mark_sent.side_effect = mark_sent
        response = ChatResponse(text="图", metadata={"attach_pictures": [self.data], "turn_db_id": turn_id})
        with (
            patch.object(sender, "build_message_segment", side_effect=lambda bot, kind, value: (kind, value)),
            patch.object(sender, "build_message", side_effect=lambda bot, *segments: list(segments)),
        ):
            await sender.send_pictures(
                self.service,
                request,
                response,
                SimpleNamespace(bot=object()),
                SimpleNamespace(send=AsyncMock(return_value={"message_id": "sent-picture"})),
            )
        [row] = await ChatTurnItem.select_rows(tenant=tenant)
        self.assertEqual((row.visible, row.send_status, row.platform_message_id), (True, "sent", "sent-picture"))
        self.assertEqual(self.store.get(tenant)[-1]["content"], "[图片]")
        self.assertEqual(self.store.get(tenant)[-1]["_quote_ids"], [])
