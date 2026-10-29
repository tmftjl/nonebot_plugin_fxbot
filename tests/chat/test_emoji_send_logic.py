"""新表情包发送逻辑：规划器挑表情、回复后按哈希发送。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.plugins.nonebot_plugin_fxbot.chat import types as chat_types
from src.plugins.nonebot_plugin_fxbot.chat.message_receive import sender
from src.plugins.nonebot_plugin_fxbot.config.system_defaults import (
    SYSTEM_DEFAULTS,
    validate_system_config,
)


def _request() -> chat_types.ChatRequest:
    return chat_types.ChatRequest(
        session_id="test:emoji-send",
        user_id="u1",
        group_id="g1",
        text="早上好",
        turn_id="t1",
        dedupe_key="d1",
        trigger_kind="mention",
    )


class ConfigTests(unittest.TestCase):
    """开关默认关闭，只接受布尔值。"""

    def test_default_off_and_validation(self) -> None:
        self.assertFalse(SYSTEM_DEFAULTS["chat"]["emoji"]["use_new_send_logic"])
        validate_system_config({"chat": {"emoji": {"use_new_send_logic": True}}})
        with self.assertRaisesRegex(ValueError, "use_new_send_logic"):
            validate_system_config({"chat": {"emoji": {"use_new_send_logic": "yes"}}})


class SendEmojiTests(unittest.IsolatedAsyncioTestCase):
    """按哈希发送选中的表情；新逻辑下不再自动匹配。"""

    def setUp(self) -> None:
        self.row = SimpleNamespace(hash="hash-a", path="a.png", description="开心")
        self.matcher = SimpleNamespace(send=AsyncMock())
        self.runtime = SimpleNamespace(bot=object())
        self.request = _request()
        self.service = SimpleNamespace(
            is_current=lambda *args: True, is_latest=lambda *args: True, mark_sent=AsyncMock()
        )
        self.addCleanup(patch.stopall)
        patch.object(sender, "build_message_segment", return_value="segment").start()
        patch.object(sender, "build_message", return_value="payload").start()
        patch.object(sender, "mark_used", AsyncMock()).start()

    async def _send(self, metadata: dict[str, object]) -> None:
        response = chat_types.ChatResponse(text="hi", metadata=dict(metadata))
        await sender.send_emoji(self.service, self.request, response, self.runtime, self.matcher, 1)

    async def test_attached_emoji_is_sent(self) -> None:
        with (
            patch.object(sender, "emoji_by_hash", AsyncMock(return_value=self.row)),
        ):
            await self._send({"attach_emoji": "hash-a"})
        self.matcher.send.assert_awaited_once_with("payload")
        self.service.mark_sent.assert_awaited_once()

    async def test_new_logic_without_attachment_sends_nothing(self) -> None:
        await self._send({})
        self.matcher.send.assert_not_awaited()

    async def test_missing_attachment_stays_quiet(self) -> None:
        with (
            patch.object(sender, "emoji_by_hash", AsyncMock(return_value=None)),
        ):
            await self._send({"attach_emoji": "gone"})
        self.matcher.send.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
