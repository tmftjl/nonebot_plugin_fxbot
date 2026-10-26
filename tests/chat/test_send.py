"""回复拆分与发送节奏测试。"""

from __future__ import annotations

import unittest

from src.plugins.nonebot_plugin_fxbot.chat.behavior.send import (
    MAX_PARTS,
    TARGET_CHARS,
    PLATFORM_MAX_CHARS,
    part_delay,
    split_reply,
)


class SplitReplyTests(unittest.TestCase):
    """按目标段长装箱，保护 URL 与代码块。"""

    def test_empty_and_short_text(self) -> None:
        self.assertEqual(split_reply(""), [])
        self.assertEqual(split_reply("  你好。  "), ["你好。"])

    def test_long_text_is_merged_into_at_most_three_parts(self) -> None:
        text = "第一段。" * 100
        parts = split_reply(text)
        self.assertEqual(len(parts), MAX_PARTS)
        self.assertEqual("".join(parts), text)
        self.assertEqual([len(part) for part in parts], [120, 120, 160])

    def test_target_length_is_respected_for_sentences(self) -> None:
        parts = split_reply("甲" * TARGET_CHARS + "。" + "乙" * 5 + "。")
        self.assertEqual(parts, ["甲" * TARGET_CHARS + "。", "乙" * 5 + "。"])

    def test_url_is_never_cut(self) -> None:
        url = "https://example.com/" + "a" * 40
        text = "前" * 110 + " " + url + "。"
        parts = split_reply(text)
        self.assertEqual("".join(parts), text)
        self.assertTrue(any(part.strip().rstrip("。") == url.strip() for part in parts))

    def test_code_block_is_never_cut(self) -> None:
        code = "```python\n" + "print('hi')\n" * 5 + "```"
        text = "看代码：" + "说明" * 40 + code
        parts = split_reply(text)
        self.assertEqual("".join(parts), text)
        self.assertTrue(any(code in part for part in parts))

    def test_platform_hard_limit_splits_overflow(self) -> None:
        text = "啊" * (PLATFORM_MAX_CHARS * 3 + 500)
        parts = split_reply(text)
        self.assertEqual("".join(parts), text)
        self.assertEqual(
            [len(part) for part in parts], [PLATFORM_MAX_CHARS, PLATFORM_MAX_CHARS, PLATFORM_MAX_CHARS, 500]
        )
        self.assertGreater(len(parts), MAX_PARTS)


class PartDelayTests(unittest.TestCase):
    """后续分段间隔随长度增长，群聊封顶、私聊减半。"""

    def test_group_delay_grows_and_caps(self) -> None:
        self.assertAlmostEqual(part_delay("短"), 0.6 + 0.005 * 1)
        self.assertEqual(part_delay("长" * 400), 2.0)

    def test_private_delay_is_halved(self) -> None:
        self.assertAlmostEqual(part_delay("长" * 400, is_group=False), 1.0)
        self.assertAlmostEqual(part_delay("短", is_group=False), (0.6 + 0.005) / 2)


if __name__ == "__main__":
    unittest.main()
