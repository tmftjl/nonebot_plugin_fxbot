"""人格提示词拼装测试。"""

from __future__ import annotations

import unittest

from src.plugins.nonebot_plugin_fxbot.chat.personas import compose_persona_prompt

STRUCTURED = """你的名字是雪球。

## 人格特征
冷淡。

## 行为风格
先查再答。

## 回复风格
说人话。
"""

TAIL = "请注意不要输出多余内容(包括不必要的前后缀，冒号，括号，表情包，@等 )，只输出发言内容就好。"
LEAD = "现在请你读读之前的聊天记录，把握当前的话题，然后给出日常且口语化的回复，"


class ComposePersonaPromptTests(unittest.TestCase):
    """按 MaiBot 的 personality / behavior_style / reply_style 结构拼装。"""

    def test_structured(self) -> None:
        text = compose_persona_prompt("雪球", STRUCTURED)

        self.assertEqual(
            text,
            f"你的名字是雪球。\n冷淡。\n你的行为风格是：先查再答。\n{LEAD}\n说人话。\n\n{TAIL}",
        )

    def test_scene_prompt(self) -> None:
        text = compose_persona_prompt("雪球", STRUCTURED, scene_prompt="群聊要短。")

        self.assertIn("在该聊天中的注意事项：\n通用注意事项：\n群聊要短。", text)
        self.assertTrue(text.endswith(TAIL))

    def test_plain_text_is_identity(self) -> None:
        text = compose_persona_prompt("小鱼", "你是一个友好、耐心且乐于助人的 AI 助手。")

        self.assertEqual(text, f"你是一个友好、耐心且乐于助人的 AI 助手。\n{LEAD}\n\n{TAIL}")

    def test_unknown_heading_falls_back(self) -> None:
        text = compose_persona_prompt("小鱼", "你好。\n\n## 额外设定\n\n原样保留。")

        self.assertTrue(text.startswith("你好。\n\n## 额外设定\n\n原样保留。\n"))

    def test_empty_intro_uses_name(self) -> None:
        text = compose_persona_prompt("雪球", "## 人格特征\n\n冷淡。")

        self.assertTrue(text.startswith("你的名字是雪球。\n冷淡。\n"))


if __name__ == "__main__":
    unittest.main()
