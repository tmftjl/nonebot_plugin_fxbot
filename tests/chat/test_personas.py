"""人格提示词拼装测试。"""

from __future__ import annotations

import unittest
from uuid import uuid4

from src.plugins.nonebot_plugin_fxbot.chat.bindings import PERSONA, get_binding, set_binding
from src.plugins.nonebot_plugin_fxbot.chat.personas import (
    delete_persona,
    get_personas_dir,
    save_persona_text,
    compose_persona_prompt,
)

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
REFERENCE = "你可以参考【回复信息参考】中的信息，但是视情况而定，不用完全遵守。"


class ComposePersonaPromptTests(unittest.TestCase):
    """按 MaiBot 的 personality / behavior_style / reply_style 结构拼装。"""

    def test_structured(self) -> None:
        text = compose_persona_prompt("雪球", STRUCTURED)

        self.assertEqual(
            text,
            f"你的名字是雪球。\n冷淡。\n{LEAD}\n说人话。\n{REFERENCE}\n\n{TAIL}",
        )

    def test_scene_prompt(self) -> None:
        text = compose_persona_prompt("雪球", STRUCTURED, scene_prompt="群聊要短。")

        self.assertIn("在该聊天中的注意事项：\n通用注意事项：\n群聊要短。", text)
        self.assertTrue(text.endswith(TAIL))

    def test_plain_text_is_identity(self) -> None:
        text = compose_persona_prompt("小鱼", "你是一个友好、耐心且乐于助人的 AI 助手。")

        self.assertEqual(text, f"你是一个友好、耐心且乐于助人的 AI 助手。\n{LEAD}\n\n{REFERENCE}\n\n{TAIL}")

    def test_unknown_heading_falls_back(self) -> None:
        text = compose_persona_prompt("小鱼", "你好。\n\n## 额外设定\n\n原样保留。")

        self.assertTrue(text.startswith("你好。\n\n## 额外设定\n\n原样保留。\n"))

    def test_empty_intro_uses_name(self) -> None:
        text = compose_persona_prompt("雪球", "## 人格特征\n\n冷淡。")

        self.assertTrue(text.startswith("你的名字是雪球。\n冷淡。\n"))


class PersonaStorageTests(unittest.TestCase):
    """编辑沿用原文件，删除人格时清掉指向它的会话绑定。"""

    def test_save_reuses_existing_txt_file(self) -> None:
        directory = get_personas_dir()
        name = f"txt-{uuid4().hex}"
        (directory / f"{name}.txt").write_text("旧内容", encoding="utf-8")

        path = save_persona_text(name, "新内容")

        self.assertEqual(path.suffix, ".txt")
        self.assertEqual((directory / f"{name}.txt").read_text(encoding="utf-8"), "新内容")
        self.assertFalse((directory / f"{name}.md").exists())

    def test_delete_clears_bindings_and_keeps_others(self) -> None:
        name = f"del-{uuid4().hex}"
        other = f"keep-{uuid4().hex}"
        save_persona_text(name, "内容")
        save_persona_text(other, "内容")
        set_binding(PERSONA, f"session-a-{name}", name)
        set_binding(PERSONA, f"session-b-{name}", name)
        set_binding(PERSONA, f"session-c-{name}", other)

        self.assertEqual(delete_persona(name), 1)

        self.assertEqual(get_binding(PERSONA, f"session-a-{name}"), "")
        self.assertEqual(get_binding(PERSONA, f"session-b-{name}"), "")
        self.assertEqual(get_binding(PERSONA, f"session-c-{name}"), other)


class ConsolePersonaRouteTests(unittest.IsolatedAsyncioTestCase):
    """控制台只能修改已存在的人格，不再顺带创建。"""

    async def test_update_missing_persona_is_rejected(self) -> None:
        from src.plugins.nonebot_plugin_fxbot.console.routes import meta

        result = await meta.update_ai_persona(f"missing-{uuid4().hex}", {"desc": "内容"})

        self.assertFalse(result["success"])
        self.assertEqual(result["message"], "人格不存在")


if __name__ == "__main__":
    unittest.main()
