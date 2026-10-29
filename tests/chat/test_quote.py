"""引用规则、平台引用段与发送降级测试。"""

from __future__ import annotations

import asyncio
import unittest
from typing import Any
from contextlib import ExitStack
from unittest.mock import patch

from src.plugins.nonebot_plugin_fxbot.chat import typo as typo_module, service as service_module
from src.plugins.nonebot_plugin_fxbot.adapter import (
    PlatformAdapter,
    UnsupportedCapability,
    OneBotV11MessageAdapter,
    OneBotV12MessageAdapter,
    QQOfficialMessageAdapter,
)
from src.plugins.nonebot_plugin_fxbot.chat.tools import ToolRuntime
from src.plugins.nonebot_plugin_fxbot.chat.types import ChatRequest, ChatResponse
from src.plugins.nonebot_plugin_fxbot.chat.service import ChatService
from src.plugins.nonebot_plugin_fxbot.chat.session import ChatSessionStore
from src.plugins.nonebot_plugin_fxbot.chat.behavior.quote import (
    QuoteCounter,
    decide_quote,
    quote_probability,
    normalize_quote_frequency,
    private_quote_probability,
)
from src.plugins.nonebot_plugin_fxbot.chat.message_receive import sender as sender_module


def _request(**values: object) -> ChatRequest:
    """构造一个带平台消息 ID 的群聊请求。"""
    fields: dict[str, object] = {
        "session_id": "tenant",
        "user_id": "user",
        "group_id": "group",
        "text": "你好",
        "turn_id": "turn",
        "dedupe_key": "msg:1",
        "trigger_kind": "mention",
        "message_id": "100",
    }
    fields.update(values)
    return ChatRequest(**fields)  # type: ignore[arg-type]


class QuoteFrequencyTests(unittest.TestCase):
    """频率档位、距离系数与抽签规则。"""

    def test_frequency_normalization(self) -> None:
        self.assertEqual(normalize_quote_frequency(" OFTEN "), "often")
        for value in ("", None, "sometimes"):
            self.assertEqual(normalize_quote_frequency(value), "occasionally")

    def test_group_probability(self) -> None:
        common = dict(text_length=10, previous_sender_self=False, previous_seconds=120.0, target_distance=1)
        occasional = quote_probability(frequency="occasionally", **common)
        self.assertEqual(quote_probability(frequency="often", **common), min(1.0, occasional * 2))
        self.assertEqual(quote_probability(frequency="never", **common), 0.0)
        # 目标就是上一条时命中率减半，距离越远系数越大
        self.assertLess(quote_probability(frequency="occasionally", **{**common, "target_distance": 0}), occasional)
        self.assertGreater(quote_probability(frequency="occasionally", **{**common, "target_distance": 9}), occasional)
        with self.assertRaises(ValueError):
            quote_probability(frequency="sometimes", **common)

    def test_private_probability_grows_with_distance(self) -> None:
        common = dict(
            text_length=10,
            previous_sender_self=False,
            previous_seconds=120.0,
            previous_burst=1,
            frequency="occasionally",
        )
        near = private_quote_probability(target_distance=0, **common)
        far = private_quote_probability(target_distance=5, **common)
        self.assertGreater(far, near)
        self.assertLessEqual(private_quote_probability(target_distance=1000, **common), 1.0)
        self.assertEqual(private_quote_probability(target_distance=0, **{**common, "frequency": "never"}), 0.0)

    def test_decide_quote(self) -> None:
        self.assertEqual(
            decide_quote(context=None, bot_user_id="bot", text="你好", frequency="never", is_group=True), (False, 0.0)
        )
        # 目标已离开窗口或未登记时直接引用
        self.assertEqual(
            decide_quote(context=None, bot_user_id="bot", text="你好", frequency="often", is_group=True), (True, 1.0)
        )

    def test_counter_snapshot(self) -> None:
        counter = QuoteCounter()
        counter.record("m1", "user")
        counter.record("m2", "user")
        counter.record("m2", "user")
        snapshot = counter.snapshot("m1")
        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(snapshot.target_distance, 1)
        self.assertEqual(snapshot.previous_sender_id, "user")
        self.assertEqual(snapshot.previous_burst, 2)
        self.assertIsNone(counter.snapshot("missing"))

    def test_counter_drops_old_targets(self) -> None:
        counter = QuoteCounter()
        counter.record("old", "user")
        for index in range(100):
            counter.record(f"m{index}", "user")
        self.assertIsNone(counter.snapshot("old"))


class _BaseAdapter(PlatformAdapter):
    """只实现抽象方法的最小适配器。"""

    def match(self, bot: Any) -> bool:
        return False

    def build_segment(self, bot: Any, segment_type: str, data: Any = None) -> Any:
        return (segment_type, data)

    def build_message(self, bot: Any, segments: list[Any]) -> Any:
        return segments

    async def send_message_to_target(self, bot: Any, target: dict[str, Any], message: Any) -> Any:
        return None


class ReplySegmentTests(unittest.TestCase):
    """各平台引用段的构造与不支持时的报错。"""

    def test_base_adapter_does_not_support_reply(self) -> None:
        adapter = _BaseAdapter()
        self.assertFalse(adapter.supports_reply())
        with self.assertRaises(UnsupportedCapability):
            adapter.build_reply_segment(object(), "100")

    def test_onebot11_reply_segment(self) -> None:
        adapter = OneBotV11MessageAdapter()
        segment = adapter.build_reply_segment(object(), "100")
        self.assertTrue(adapter.supports_reply())
        self.assertEqual((segment.type, segment.data), ("reply", {"id": "100"}))

    def test_onebot12_reply_segment(self) -> None:
        adapter = OneBotV12MessageAdapter()
        segment = adapter.build_reply_segment(object(), "100")
        self.assertTrue(adapter.supports_reply())
        self.assertEqual((segment.type, segment.data["message_id"]), ("reply", "100"))

    def test_qq_reference_segment(self) -> None:
        adapter = QQOfficialMessageAdapter()
        segment = adapter.build_reply_segment(object(), "100")
        self.assertTrue(adapter.supports_reply())
        self.assertEqual(segment.type, "reference")
        self.assertEqual(segment.data["reference"].message_id, "100")


class _Config:
    """只提供历史上限。"""

    def get_system(self) -> dict[str, object]:
        return {"chat": {"max_context_size": 10}}


class _TypoSilent:
    """断言原文的发送用例关闭错字模拟。"""

    def get_system(self) -> dict[str, object]:
        return {"chat": {"typo": {"enable": False}}}


class _Matcher:
    """记录发送载荷。"""

    def __init__(self) -> None:
        self.sent: list[Any] = []

    async def send(self, payload: Any) -> Any:
        await asyncio.sleep(0)
        self.sent.append(payload)
        return None


class SendWithQuoteTests(unittest.IsolatedAsyncioTestCase):
    """首段带引用；构造失败或不支持时纯文本降级。"""

    def _response(self) -> ChatResponse:
        return ChatResponse(
            text="第一段。第二段。",
            epoch=0,
            metadata={"reply_parts": ["第一段。", "第二段。"]},
        )

    def _patches(self, stack: ExitStack, *, supported: bool, failure: bool, quote: bool = True) -> None:
        def build_message(_bot: Any, *segments: Any) -> tuple[Any, ...]:
            return ("quoted", *segments)

        stack.enter_context(patch.object(service_module, "get_config_manager", return_value=_Config()))
        stack.enter_context(patch.object(typo_module, "get_config_manager", return_value=_TypoSilent()))
        stack.enter_context(patch.object(sender_module, "typing_time", return_value=0.0))
        stack.enter_context(patch.object(service_module.memory_state, "is_ready", return_value=False))
        stack.enter_context(patch.object(sender_module, "decide_quote", return_value=(quote, 1.0 if quote else 0.0)))
        stack.enter_context(patch.object(sender_module, "supports_reply", return_value=supported))
        stack.enter_context(
            patch.object(
                sender_module,
                "build_reply_segment",
                side_effect=RuntimeError("bad") if failure else lambda _bot, message_id: ("reply", message_id),
            )
        )
        stack.enter_context(
            patch.object(
                sender_module,
                "build_message_segment",
                side_effect=lambda _bot, kind, part: (kind, part),
            )
        )
        stack.enter_context(patch.object(sender_module, "build_message", side_effect=build_message))

    async def _send(
        self, request: ChatRequest, *, supported: bool = True, failure: bool = False, quote: bool = True
    ) -> tuple[bool, list[Any]]:
        matcher = _Matcher()
        service = ChatService(ChatSessionStore())
        with ExitStack() as stack:
            self._patches(stack, supported=supported, failure=failure, quote=quote)
            result = await service._send_parts(
                request, self._response(), ToolRuntime(bot=object(), matcher=matcher), matcher
            )
        return result, matcher.sent

    async def test_only_first_part_is_quoted(self) -> None:
        result, sent = await self._send(_request())

        self.assertTrue(result)
        self.assertEqual(sent[0], ("quoted", ("reply", "100"), ("text", "第一段。")))
        self.assertEqual(sent[1], "第二段。")

    async def test_unsupported_platform_falls_back_to_text(self) -> None:
        result, sent = await self._send(_request(), supported=False)

        self.assertTrue(result)
        self.assertEqual(sent, ["第一段。", "第二段。"])

    async def test_construction_failure_does_not_block_sending(self) -> None:
        result, sent = await self._send(_request(), failure=True)

        self.assertTrue(result)
        self.assertEqual(sent, ["第一段。", "第二段。"])

    async def test_not_quoting_sends_plain_text(self) -> None:
        result, sent = await self._send(_request(), quote=False)

        self.assertTrue(result)
        self.assertEqual(sent, ["第一段。", "第二段。"])

    async def test_decide_quote_uses_counter_snapshot(self) -> None:
        counter = QuoteCounter()
        counter.record("100", "user")
        counter.record("200", "user")
        matcher = _Matcher()

        with ExitStack() as stack:
            self._patches(stack, supported=True, failure=False)
            stack.enter_context(patch.object(sender_module, "quote_counter", return_value=counter))
            decide = stack.enter_context(patch.object(sender_module, "decide_quote", return_value=(True, 1.0)))
            await ChatService(ChatSessionStore())._send_parts(
                _request(), self._response(), ToolRuntime(bot=object()), matcher
            )

        context = decide.call_args.kwargs["context"]
        self.assertEqual(context.target_distance, 1)
        self.assertTrue(decide.call_args.kwargs["is_group"])
