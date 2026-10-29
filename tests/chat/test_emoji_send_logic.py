"""新表情包发送逻辑：规划器挑表情、回复后按哈希发送。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.plugins.nonebot_plugin_fxbot.chat import types as chat_types, planner
from src.plugins.nonebot_plugin_fxbot.chat.heart_flow import plan_flow
from src.plugins.nonebot_plugin_fxbot.chat.message_receive import sender
from src.plugins.nonebot_plugin_fxbot.config.system_defaults import (
    SYSTEM_DEFAULTS,
    validate_system_config,
)


class _Lease:
    def __init__(self, value: object) -> None:
        self.value = value

    async def __aenter__(self) -> object:
        return self.value

    async def __aexit__(self, *exc: object) -> None:
        return None


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


class PlannerEmojiTests(unittest.TestCase):
    """候选拼图进入规划输入，解析时把序号映射回哈希。"""

    def test_prompt_attaches_collage(self) -> None:
        prompt = planner.build_prompt([], [], emoji_image="data:image/png;base64,AAAA")
        text, image = prompt[-1]["content"]
        self.assertIn("emoji_index", text["text"])
        self.assertEqual(image, {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}})
        self.assertNotIn("emoji_index", planner.build_prompt([], [])[-1]["content"])

    def test_parse_maps_index(self) -> None:
        def parsed(index: object, hashes: list[str] | None = None) -> str:
            plan = planner.parse_plan({"action": "text_reply", "emoji_index": index}, [], emoji_hashes=hashes)
            return plan.emoji_hash  # type: ignore[union-attr]

        self.assertEqual(parsed(2, ["a", "b"]), "b")
        self.assertEqual(parsed(3, ["a"]), "")
        self.assertEqual(parsed(1), "")
        self.assertEqual(parsed("x", ["a"]), "")


class PlannerImageFallbackTests(unittest.IsolatedAsyncioTestCase):
    """规划模型不支持图片时退回纯文本规划，本轮不挑表情。"""

    async def test_image_failure_retries_without_collage(self) -> None:
        provider = SimpleNamespace(
            chat_json=AsyncMock(side_effect=[RuntimeError("bad image"), {"action": "text_reply", "emoji_index": 1}])
        )
        plan = await planner.request_plan(
            provider, [], [], emoji_image="data:image/png;base64,AAAA", emoji_hashes=["a"]
        )
        self.assertIsNotNone(plan)
        self.assertEqual(plan.emoji_hash, "")  # type: ignore[union-attr]
        retried = provider.chat_json.await_args_list[1].args[0]
        self.assertIsInstance(retried[-1]["content"], str)


class MaybePlanEmojiTests(unittest.IsolatedAsyncioTestCase):
    """开启新逻辑时把候选取样交给规划器。"""

    async def test_catalog_passed_to_planner(self) -> None:
        row = SimpleNamespace(hash="hash-a", description="开心大笑")
        plan = planner.Plan("text_reply", emoji_hash="hash-a")
        request_plan = AsyncMock(return_value=plan)
        with (
            patch.object(plan_flow, "planner_config", return_value={"mode": "complex", "wait_seconds": 60}),
            patch.object(plan_flow, "emoji_config", return_value={"use_new_send_logic": True}),
            patch.object(plan_flow, "emoji_catalog", AsyncMock(return_value=[row])),
            patch.object(plan_flow, "emoji_collage", return_value="data:image/png;base64,AAAA"),
            patch.object(plan_flow, "request_plan_with_steering", request_plan),
            patch.object(plan_flow, "fallback_provider", lambda *a, **k: _Lease(object())),
            patch.object(plan_flow, "plan_features", return_value={}),
            patch.object(plan_flow, "record_plan_sample", AsyncMock()),
        ):
            result = await plan_flow.maybe_plan(_request(), [], [])
        self.assertEqual(result.emoji_hash, "hash-a")
        kwargs = request_plan.await_args.kwargs
        self.assertEqual(kwargs["emoji_hashes"], ["hash-a"])
        self.assertEqual(kwargs["emoji_image"], "data:image/png;base64,AAAA")

    async def test_disabled_skips_catalog(self) -> None:
        request_plan = AsyncMock(return_value=planner.Plan("text_reply"))
        with (
            patch.object(plan_flow, "planner_config", return_value={"mode": "complex", "wait_seconds": 60}),
            patch.object(plan_flow, "emoji_config", return_value={"use_new_send_logic": False}),
            patch.object(plan_flow, "emoji_catalog", AsyncMock()) as catalog,
            patch.object(plan_flow, "request_plan_with_steering", request_plan),
            patch.object(plan_flow, "fallback_provider", lambda *a, **k: _Lease(object())),
            patch.object(plan_flow, "plan_features", return_value={}),
            patch.object(plan_flow, "record_plan_sample", AsyncMock()),
        ):
            await plan_flow.maybe_plan(_request(), [], [])
        catalog.assert_not_awaited()
        self.assertIsNone(request_plan.await_args.kwargs["emoji_image"])


class SendEmojiTests(unittest.IsolatedAsyncioTestCase):
    """按哈希发送选中的表情；新逻辑下不再自动匹配。"""

    def setUp(self) -> None:
        self.row = SimpleNamespace(hash="hash-a", path="a.png")
        self.matcher = SimpleNamespace(send=AsyncMock())
        self.runtime = SimpleNamespace(bot=object())
        self.request = SimpleNamespace(session_id="test:emoji-send")
        self.addCleanup(patch.stopall)
        patch.object(sender, "build_message_segment", return_value="segment").start()
        patch.object(sender, "build_message", return_value="payload").start()
        patch.object(sender, "mark_used", AsyncMock()).start()

    async def _send(self, metadata: dict[str, object]) -> None:
        response = chat_types.ChatResponse(text="hi", metadata=dict(metadata))
        await sender.send_emoji(None, self.request, response, self.runtime, self.matcher, 1)

    async def test_attached_emoji_is_sent(self) -> None:
        with (
            patch.object(sender, "emoji_config", return_value={"use_new_send_logic": True}),
            patch.object(sender, "emoji_by_hash", AsyncMock(return_value=self.row)),
            patch.object(sender, "pick_emoji", AsyncMock()) as pick,
        ):
            await self._send({"attach_emoji": "hash-a"})
        self.matcher.send.assert_awaited_once_with("payload")
        pick.assert_not_awaited()

    async def test_new_logic_without_attachment_sends_nothing(self) -> None:
        with patch.object(sender, "emoji_config", return_value={"use_new_send_logic": True}):
            await self._send({})
        self.matcher.send.assert_not_awaited()

    async def test_old_logic_matches_reply_text(self) -> None:
        with (
            patch.object(sender, "emoji_config", return_value={"use_new_send_logic": False}),
            patch.object(sender, "pick_emoji", AsyncMock(return_value=self.row)) as pick,
        ):
            await self._send({})
        pick.assert_awaited_once()
        self.matcher.send.assert_awaited_once_with("payload")

    async def test_missing_attachment_stays_quiet(self) -> None:
        with (
            patch.object(sender, "emoji_config", return_value={"use_new_send_logic": True}),
            patch.object(sender, "emoji_by_hash", AsyncMock(return_value=None)),
        ):
            await self._send({"attach_emoji": "gone"})
        self.matcher.send.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
