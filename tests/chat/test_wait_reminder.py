"""连续等待提醒：三次 wait 后提示规划器结束或延长等待。"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from src.plugins.nonebot_plugin_fxbot.chat import types as chat_types, planner
from src.plugins.nonebot_plugin_fxbot.chat.heart_flow import plan_flow


class _Lease:
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *exc: object) -> None:
        return None


def _request() -> chat_types.ChatRequest:
    return chat_types.ChatRequest(
        session_id="test:wait",
        user_id="u1",
        group_id="g1",
        text="在吗",
        turn_id="t1",
        dedupe_key="d1",
        trigger_kind="mention",
    )


class WaitReminderTests(unittest.TestCase):
    """计数只认 wait，其他动作清零。"""

    def setUp(self) -> None:
        plan_flow._consecutive_waits.clear()

    def test_reminder_starts_after_three_waits(self) -> None:
        for _ in range(2):
            plan_flow.note_plan("s", "wait")
        self.assertEqual(plan_flow.wait_reminder("s"), "")
        plan_flow.note_plan("s", "wait")
        self.assertIn("end_conversation", plan_flow.wait_reminder("s"))
        plan_flow.note_plan("s", "text_reply")
        self.assertEqual(plan_flow.wait_reminder("s"), "")


class MaybePlanReminderTests(unittest.IsolatedAsyncioTestCase):
    """连续等待的提醒进入规划提示。"""

    def setUp(self) -> None:
        plan_flow._consecutive_waits["test:wait"] = 3
        self.addCleanup(plan_flow._consecutive_waits.clear)

    async def test_reminder_passed_as_hint(self) -> None:
        request_plan = AsyncMock(return_value=planner.Plan("wait"))
        with (
            patch.object(plan_flow, "planner_config", return_value={"mode": "complex", "wait_seconds": 60}),
            patch.object(plan_flow, "emoji_config", return_value={}),
            patch.object(plan_flow, "request_plan_with_steering", request_plan),
            patch.object(plan_flow, "fallback_provider", lambda *a, **k: _Lease()),
            patch.object(plan_flow, "plan_features", return_value={}),
            patch.object(plan_flow, "record_plan_sample", AsyncMock()),
        ):
            await plan_flow.maybe_plan(_request(), [], [])
        hint = request_plan.await_args.kwargs["hint"]
        self.assertIn("连续等待", hint)
        self.assertEqual(plan_flow._consecutive_waits["test:wait"], 4)


if __name__ == "__main__":
    unittest.main()
