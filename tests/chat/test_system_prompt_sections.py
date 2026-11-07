"""独立系统提示词段的排序、同步约束及失败封闭测试。"""

from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from src.plugins.nonebot_plugin_fxbot.chat import hooks


class SystemPromptSectionTests(unittest.TestCase):
    """系统段只通过不可变贡献值插入，不能回写人格、历史或身份。"""

    def setUp(self) -> None:
        self.enterContext(patch.object(hooks, "_hooks", {}))
        self.context = hooks.ChatHookContext("test:private:user")

    def test_empty_default_and_numeric_then_name_order(self) -> None:
        self.assertEqual(hooks.system_prompt_sections(self.context), "")
        for name, order in (("z", 100), ("a", 100), ("first", -1), ("last", 500)):
            hooks.register_chat_hook(
                "system_prompt", name, lambda context, name=name, order=order: hooks.SystemPromptSection(name, order)
            )
        self.assertEqual(hooks.system_prompt_sections(self.context), "first\n\na\n\nz\n\nlast")

    def test_none_and_blank_are_ignored_without_changing_literal_text(self) -> None:
        hooks.register_chat_hook("system_prompt", "none", lambda context: None)
        hooks.register_chat_hook("system_prompt", "blank", lambda context: hooks.SystemPromptSection("  "))
        hooks.register_chat_hook("system_prompt", "literal", lambda context: hooks.SystemPromptSection("{{literal}}"))
        self.assertEqual(hooks.system_prompt_sections(self.context), "{{literal}}")

    def test_invalid_values_and_exceptions_stop_generation(self) -> None:
        for callback in (
            lambda context: "wrong type",
            lambda context: hooks.SystemPromptSection(None),
            Mock(side_effect=RuntimeError("test")),
            *(
                lambda context, value=value: hooks.SystemPromptSection("x", value)
                for value in (True, "100", float("nan"), float("inf"))
            ),
        ):
            hooks.register_chat_hook("system_prompt", "invalid", callback)
            with self.assertRaises(hooks.ChatHookError):
                hooks.system_prompt_sections(self.context)

    def test_async_callbacks_are_rejected(self) -> None:
        async def callback(context):
            return hooks.SystemPromptSection("wrong")

        with self.assertRaises(ValueError):
            hooks.register_chat_hook("system_prompt", "async", callback)
        hooks.register_chat_hook("system_prompt", "wrapped", lambda context: callback(context))
        with self.assertRaises(hooks.ChatHookError):
            hooks.system_prompt_sections(self.context)

    def test_replacement_and_unregister_remain_precise(self) -> None:
        old = hooks.register_chat_hook("system_prompt", "test", lambda context: hooks.SystemPromptSection("old"))
        new = hooks.register_chat_hook("system_prompt", "test", lambda context: hooks.SystemPromptSection("new"))
        old()
        self.assertEqual(hooks.system_prompt_sections(self.context), "new")
        new()
        new()
        self.assertEqual(hooks.system_prompt_sections(self.context), "")
