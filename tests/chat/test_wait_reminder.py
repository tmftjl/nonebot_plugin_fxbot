"""连续等待提醒：三次 wait 后提示规划器结束或延长等待。"""

from __future__ import annotations

import unittest

from src.plugins.nonebot_plugin_fxbot.chat.heart_flow import plan_flow


class WaitReminderTests(unittest.TestCase):
    """计数只认 wait，其他动作清零。"""

    def setUp(self) -> None:
        plan_flow._consecutive_waits.clear()

    def test_reminder_starts_after_three_waits(self) -> None:
        for _ in range(2):
            plan_flow.note_plan("s", "wait")
        self.assertEqual(plan_flow.wait_reminder("s"), "")
        plan_flow.note_plan("s", "wait")
        self.assertIn("stop", plan_flow.wait_reminder("s"))
        plan_flow.note_plan("s", "text_reply")
        self.assertEqual(plan_flow.wait_reminder("s"), "")


if __name__ == "__main__":
    unittest.main()
