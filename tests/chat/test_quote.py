"""引用规则、平台引用段与发送降级测试。"""

from __future__ import annotations

import asyncio
import unittest
from typing import Any
from contextlib import ExitStack
from unittest.mock import patch

from src.plugins.nonebot_plugin_fxbot.chat import service as service_module
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
from src.plugins.nonebot_plugin_fxbot.chat.behavior.quote import should_quote, normalize_quote_mode


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


class QuotePolicyTests(unittest.TestCase):
    """auto/always/never 与上下文触发。"""

    def test_mode_normalization(self) -> None:
        self.assertEqual(normalize_quote_mode(" ALWAYS "), "always")
        for value in ("", None, "sometimes"):
            self.assertEqual(normalize_quote_mode(value), "auto")

    def test_auto_needs_context(self) -> None:
        self.assertFalse(should_quote(_request()))
        self.assertTrue(should_quote(_request(quoted_text="引用")))
        self.assertTrue(should_quote(_request(source_author_ids=["a", "b"])))
        self.assertTrue(should_quote(_request(), has_newer=True))

    def test_private_defaults_to_no_quote(self) -> None:
        private = _request(group_id=None, quoted_text="引用")
        self.assertFalse(should_quote(private))
        self.assertTrue(should_quote(private, mode="always"))

    def test_always_and_never(self) -> None:
        self.assertTrue(should_quote(_request(), mode="always"))
        self.assertFalse(should_quote(_request(quoted_text="引用"), mode="never"))
        self.assertFalse(should_quote(_request(message_id=None), mode="always"))


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
    """只提供引用模式与历史上限。"""

    def __init__(self, mode: str = "always") -> None:
        self.mode = mode

    def get_system(self) -> dict[str, object]:
        return {"chat": {"max_history": 10, "behavior": {"quote_mode": self.mode}}}


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

    def _patches(self, stack: ExitStack, *, supported: bool, failure: bool, mode: str) -> None:
        def build_message(_bot: Any, *segments: Any) -> tuple[Any, ...]:
            return ("quoted", *segments)

        stack.enter_context(patch.object(service_module, "get_config_manager", return_value=_Config(mode)))
        stack.enter_context(patch.object(service_module, "part_delay", return_value=0.0))
        stack.enter_context(patch.object(service_module.memory_state, "is_ready", return_value=False))
        stack.enter_context(patch.object(service_module, "supports_reply", return_value=supported))
        stack.enter_context(
            patch.object(
                service_module,
                "build_reply_segment",
                side_effect=RuntimeError("bad") if failure else lambda _bot, message_id: ("reply", message_id),
            )
        )
        stack.enter_context(
            patch.object(
                service_module,
                "build_message_segment",
                side_effect=lambda _bot, kind, part: (kind, part),
            )
        )
        stack.enter_context(patch.object(service_module, "build_message", side_effect=build_message))

    async def _send(
        self, request: ChatRequest, *, supported: bool = True, failure: bool = False, mode: str = "always"
    ) -> tuple[bool, list[Any]]:
        matcher = _Matcher()
        service = ChatService(ChatSessionStore())
        with ExitStack() as stack:
            self._patches(stack, supported=supported, failure=failure, mode=mode)
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

    async def test_never_mode_skips_quote(self) -> None:
        result, sent = await self._send(_request(), mode="never")

        self.assertTrue(result)
        self.assertEqual(sent, ["第一段。", "第二段。"])

    async def test_newer_message_triggers_quote_in_auto_mode(self) -> None:
        store = ChatSessionStore()
        store.mark_seen("tenant", "msg:trigger")
        request = _request(activity_seq=store.activity("tenant"))
        # 生成期间同会话又来了新消息，回复应带引用
        store.mark_seen("tenant", "msg:newer")
        matcher = _Matcher()

        with ExitStack() as stack:
            self._patches(stack, supported=True, failure=False, mode="auto")
            await ChatService(store)._send_parts(request, self._response(), ToolRuntime(bot=object()), matcher)

        self.assertEqual(matcher.sent[0], ("quoted", ("reply", "100"), ("text", "第一段。")))
