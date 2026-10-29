"""五类工具媒体的上下文投影和图片索引约束。"""

from __future__ import annotations

import json
import base64
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.plugins.nonebot_plugin_fxbot.chat.session import ChatSessionStore
from src.plugins.nonebot_plugin_fxbot.chat.heart_flow.attachments import tool_media, resolve_pictures


class ToolMediaTests(unittest.IsolatedAsyncioTestCase):
    """非图片媒体保留元信息，二进制只供有效图片引用使用。"""

    async def test_five_media_types_keep_indexes_without_exposing_binary_text(self) -> None:
        binary = base64.b64encode(b"private-binary-body").decode("ascii")
        picture = base64.b64encode(b"synthetic-image").decode("ascii")
        result = {
            "ok": True,
            "result": {
                "content_items": [
                    {"content_type": "text", "text": "查询完成"},
                    {"content_type": "audio", "data": binary, "mime_type": "audio/wav", "name": "语音"},
                    {"content_type": "resource_link", "uri": "https://example.com/report"},
                    {"content_type": "resource", "name": "说明", "text": "资源中的正文"},
                    {"content_type": "binary", "data": binary, "mime_type": "application/pdf", "name": "报告"},
                    {"content_type": "binary", "data": f"data:image/png;base64,{picture}", "mime_type": "image/png"},
                    {"content_type": "image", "data": "!!!", "mime_type": "image/png"},
                ]
            },
        }
        projected, additions = tool_media(result, "call-media")
        self.assertEqual(
            projected["result"]["media_indexes"], [f"tool_result:call-media:{index}" for index in range(2, 8)]
        )
        text = json.dumps(projected, ensure_ascii=False)
        self.assertIn("资源中的正文", text)
        self.assertIn("https://example.com/report", text)
        self.assertNotIn(binary, text)
        self.assertNotIn(picture, text)
        images = [part for message in additions for part in message["content"] if part["type"] == "image_url"]
        self.assertEqual(len(images), 1)
        request = SimpleNamespace(session_id="session-a", message_id="m1", dedupe_key="m1", metadata={}, images=[])
        service = SimpleNamespace(session_store=ChatSessionStore())
        self.assertEqual(
            await resolve_pictures(service, request, [{"media_index": "tool_result:call-media:6"}], additions),
            [b"synthetic-image"],
        )
        with self.assertRaises(ValueError):
            await resolve_pictures(service, request, [{"media_index": "tool_result:call-media:2"}], additions)

    def test_remote_image_uri_is_not_fetched_or_embedded(self) -> None:
        result = {
            "ok": True,
            "result": {
                "content_items": [
                    {
                        "content_type": "image",
                        "uri": "http://127.0.0.1/private",
                        "name": '图"<',
                        "metadata": {
                            "source_url": 'https://example.com/?a=1&b="x"',
                            "context_key": {"not": "a scalar"},
                        },
                    }
                ]
            },
        }
        projected, [message] = tool_media(result, "call")
        self.assertEqual([part["type"] for part in message["content"]], ["text"])
        self.assertEqual(message["_tool_media"], {})
        self.assertIn("&quot;", projected["result"]["content"])
        self.assertNotIn("context_key", projected["result"]["content"])

    async def test_attachment_schema_does_not_accept_previous_string_form(self) -> None:
        with patch(
            "src.plugins.nonebot_plugin_fxbot.chat.heart_flow.attachments.chat_repository.message_by_identity",
            AsyncMock(),
        ) as lookup:
            with self.assertRaises(ValueError):
                await resolve_pictures(SimpleNamespace(), SimpleNamespace(), "m1", [])
        lookup.assert_not_awaited()
