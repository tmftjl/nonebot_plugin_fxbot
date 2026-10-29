"""回复断句、后处理与打字节奏测试（流程对齐 MaiBot）。"""

from __future__ import annotations

import random
import unittest
from types import SimpleNamespace
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from src.plugins.nonebot_plugin_fxbot.chat import service as service_module
from src.plugins.nonebot_plugin_fxbot.chat.tools import ToolRuntime
from src.plugins.nonebot_plugin_fxbot.chat.types import ChatRequest, ChatResponse
from src.plugins.nonebot_plugin_fxbot.chat.session import ChatSessionStore
from src.plugins.nonebot_plugin_fxbot.chat.message_receive import sender as sender_module
from src.plugins.nonebot_plugin_fxbot.chat.behavior.response import (
    ProcessedResponseSegment,
    typing_time,
    protect_kaomoji,
    recover_kaomoji,
    process_response,
)


class _NoMerge(random.Random):
    """固定随机数，避免概率合并让断言不稳定。"""

    def random(self) -> float:  # noqa: D102 - 测试替身
        return 0.99


class ResponsePipelineTests(unittest.TestCase):
    """断句结果、兜底与分段压缩。"""

    def test_empty_text_uses_maibot_fallback(self) -> None:
        self.assertEqual([item.text for item in process_response("")], ["呃呃"])
        self.assertEqual([item.text for item in process_response("  （笑）  ")], ["呃呃"])

    def test_sentence_separators_are_not_sent(self) -> None:
        segments = process_response("你好。", rng=_NoMerge())
        self.assertEqual([item.text for item in segments], ["你好"])
        self.assertEqual(segments[0].separator, "。")

    def test_splitter_disabled_keeps_whole_text(self) -> None:
        text = "你好，今天怎么样？明天见！"
        segments = process_response(text, splitter={"enable": False}, rng=_NoMerge())
        self.assertEqual([item.text for item in segments], [text])

    def test_probability_merge_and_separator(self) -> None:
        text = "你好，今天怎么样？明天见！"
        segments = process_response(text, rng=_NoMerge())
        self.assertEqual([item.text for item in segments], ["你好", "今天怎么样？明天见！"])
        merged = process_response(text, splitter={"max_split_num": 1}, rng=_NoMerge())
        self.assertEqual([item.text for item in merged], [text])

    def test_too_many_sentences_falls_back_or_returns_all(self) -> None:
        text = "你好。" * 12
        default = process_response(text, splitter={"max_sentence_num": 8}, rng=_NoMerge())
        self.assertEqual(len(default), 1)
        self.assertNotIn("你好", default[0].text)
        overflow = process_response(
            text,
            splitter={"max_sentence_num": 8, "enable_overflow_return_all": True},
            rng=_NoMerge(),
        )
        self.assertEqual(overflow, [ProcessedResponseSegment(text)])

    def test_kaomoji_is_protected(self) -> None:
        text = "(｡･ω･｡) 你好，今天怎么样？"
        protected, mapping = protect_kaomoji(text)
        self.assertNotIn("｡･ω･｡", protected)
        self.assertEqual(recover_kaomoji([protected], mapping), [text])
        segments = process_response(text, splitter={"enable_kaomoji_protection": True}, rng=_NoMerge())
        self.assertTrue(any("｡･ω･｡" in item.text for item in segments))

    def test_typing_time_matches_maibot(self) -> None:
        self.assertAlmostEqual(typing_time("短"), 1.2)
        self.assertAlmostEqual(typing_time("你好"), 0.6)
        self.assertAlmostEqual(typing_time("hello"), 0.75)
        self.assertAlmostEqual(typing_time("你好世界", is_emoji=True), 1.0)
        self.assertAlmostEqual(
            typing_time("你好", is_image=False),
            typing_time("你好", is_image=False),
        )
        self.assertEqual(typing_time("你好", is_image=True), typing_time("啊", is_emoji=True))

    def test_typing_speed_zero_skips_wait(self) -> None:
        config = patch(
            "src.plugins.nonebot_plugin_fxbot.chat.behavior.response.response_config",
            return_value=({"typing_speed": 0}, {}),
        )
        with config:
            self.assertEqual(typing_time("短"), 0.0)
            self.assertEqual(typing_time("你好"), 0.0)
            self.assertEqual(typing_time("hello"), 0.0)
            self.assertEqual(typing_time("你好世界", is_emoji=True), 0.0)
            self.assertEqual(typing_time("你好", is_image=True), 0.0)

    def test_typing_speed_one_keeps_timing(self) -> None:
        config = patch(
            "src.plugins.nonebot_plugin_fxbot.chat.behavior.response.response_config",
            return_value=({"typing_speed": 1}, {}),
        )
        with config:
            self.assertAlmostEqual(typing_time("短"), 1.2)
            self.assertAlmostEqual(typing_time("你好"), 0.6)
            self.assertAlmostEqual(typing_time("你好世界", is_emoji=True), 1.0)


class SenderReceiptTests(unittest.TestCase):
    """回执 ID 兼容字典与常见字段名。"""

    def test_shapes(self) -> None:
        self.assertEqual(sender_module._receipt_id(SimpleNamespace(message_id=42)), "42")
        self.assertEqual(sender_module._receipt_id(SimpleNamespace(id="qq-1")), "qq-1")
        self.assertEqual(sender_module._receipt_id({"message_id": 7}), "7")
        self.assertEqual(sender_module._receipt_id({"id": "dict-id"}), "dict-id")
        self.assertIsNone(sender_module._receipt_id(None))
        self.assertIsNone(sender_module._receipt_id({}))


class SenderAliveTests(unittest.IsolatedAsyncioTestCase):
    """表情、语音、纠错与正文共用同一轮次令牌。"""

    def _request(self) -> ChatRequest:
        return ChatRequest(
            session_id="tenant",
            user_id="user",
            group_id="group",
            text="你好",
            turn_id="turn",
            dedupe_key="msg:1",
            trigger_kind="mention",
        )

    async def test_first_segment_is_sent_before_typing_wait(self) -> None:
        events = []
        service = SimpleNamespace(
            session_store=ChatSessionStore(),
            is_current=lambda *args: True,
            is_latest=lambda *args: True,
            mark_sending=AsyncMock(),
            mark_sent=AsyncMock(),
            mark_send_failed=AsyncMock(),
            mark_send_aborted=AsyncMock(),
        )

        async def send(message):
            events.append(("send", message))

        async def wait(seconds):
            events.append(("wait", seconds))

        matcher = SimpleNamespace(send=send)
        with (
            patch.object(sender_module, "parts", return_value=["第一段", "第二段"]),
            patch.object(sender_module, "typing_time", return_value=3),
            patch.object(sender_module.asyncio, "sleep", new=wait),
            patch.object(
                sender_module, "apply_typo", side_effect=lambda text: SimpleNamespace(text=text, correction="")
            ),
            patch.object(sender_module, "send_voice", AsyncMock()),
            patch.object(sender_module, "send_emoji", AsyncMock()),
            patch.object(sender_module.chat_writeback, "on_reply"),
        ):
            await sender_module._send_parts(
                service, self._request(), ChatResponse(text="第一段第二段"), None, matcher, {"sent_parts": 0}
            )
        self.assertEqual(events, [("send", "第一段"), ("wait", 3), ("send", "第二段")])

    async def test_send_parts_returns_delivered_typo_text(self) -> None:
        for outcome in ("success", "first_failure", "second_failure", "superseded", "empty"):
            with self.subTest(outcome=outcome):
                service = SimpleNamespace(
                    session_store=ChatSessionStore(),
                    is_current=lambda *args: True,
                    is_latest=lambda *args: True,
                    mark_sending=AsyncMock(),
                    mark_sent=AsyncMock(),
                    mark_send_failed=AsyncMock(),
                    mark_send_aborted=AsyncMock(),
                )
                matcher = SimpleNamespace(send=AsyncMock())
                if outcome == "first_failure":
                    matcher.send.side_effect = RuntimeError("send failed")
                elif outcome == "second_failure":
                    matcher.send.side_effect = [None, RuntimeError("send failed")]
                elif outcome == "superseded":
                    service.is_latest = lambda *args: matcher.send.await_count == 0
                response = ChatResponse(text="first second", epoch=0)
                request = self._request()
                request.occurred_at = datetime(2020, 1, 1, tzinfo=timezone.utc).replace(tzinfo=None)
                with (
                    patch.object(
                        sender_module, "parts", return_value=[] if outcome == "empty" else ["first", "second"]
                    ),
                    patch.object(sender_module, "typing_time", return_value=0.0),
                    patch.object(
                        sender_module,
                        "apply_typo",
                        side_effect=lambda text: SimpleNamespace(text=text + "!", correction=""),
                    ),
                    patch.object(sender_module, "send_voice", AsyncMock()),
                    patch.object(sender_module, "send_emoji", AsyncMock()) as send_emoji,
                    patch.object(sender_module.chat_writeback, "on_reply") as writeback,
                ):
                    text = await sender_module.send_parts(service, request, response, None, matcher)
                expected = "" if outcome in ("first_failure", "empty") else "first!"
                if outcome == "success":
                    expected += "\nsecond!"
                self.assertEqual(text, expected)
                self.assertEqual(service.mark_sent.await_count, len(expected.splitlines()) if expected else 0)
                if expected:
                    self.assertEqual(writeback.call_args.kwargs["reply_text"], expected)
                    self.assertGreater(writeback.call_args.kwargs["occurred_at"], request.occurred_at)
                    self.assertIsNone(writeback.call_args.kwargs["occurred_at"].tzinfo)
                    self.assertEqual(send_emoji.await_args.args[5], service.mark_sent.await_count)
                    self.assertEqual(service.mark_sent.await_args_list[0].kwargs["content"], "first!")
                else:
                    writeback.assert_not_called()
                    send_emoji.assert_not_awaited()

    async def test_process_and_send_keeps_boolean_result(self) -> None:
        service = service_module.ChatService(ChatSessionStore())
        runtime = SimpleNamespace(matcher=SimpleNamespace(send=AsyncMock()))
        for sent_text in ("first", ""):
            with (
                self.subTest(sent_text=sent_text),
                patch.object(service, "_process_locked", AsyncMock(return_value=ChatResponse(text="first second"))),
                patch.object(service, "_send_parts", AsyncMock(return_value=sent_text)),
            ):
                self.assertIs(await service.process_and_send(self._request(), runtime), bool(sent_text))

    async def test_emoji_is_not_picked_after_turn_is_superseded(self) -> None:
        matcher = SimpleNamespace(send=AsyncMock())
        pick = AsyncMock()
        with (
            patch.object(sender_module, "emoji_by_hash", pick),
        ):
            await sender_module.send_emoji(
                service_module.chat_service,
                self._request(),
                ChatResponse(text="回复"),
                ToolRuntime(bot=object(), matcher=matcher),
                matcher,
                1,
                lambda: False,
            )
        pick.assert_not_awaited()
        matcher.send.assert_not_awaited()

    async def test_correction_is_skipped_after_turn_is_superseded(self) -> None:
        matcher = SimpleNamespace(send=AsyncMock())
        with patch.object(sender_module, "typing_time", return_value=0.0):
            await sender_module.send_correction(object(), matcher, "正", None, lambda: False)
        matcher.send.assert_not_awaited()

    async def test_voice_is_skipped_after_turn_is_superseded(self) -> None:
        matcher = SimpleNamespace(send=AsyncMock())
        with (
            patch.object(sender_module, "tts_enabled", return_value=True),
            patch.object(sender_module, "synthesize", new=AsyncMock(return_value=b"audio")),
        ):
            sent = await sender_module.send_voice(
                ToolRuntime(bot=object(), matcher=matcher), matcher, "你好", lambda: False
            )
        self.assertFalse(sent)
        matcher.send.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
