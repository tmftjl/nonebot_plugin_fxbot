"""无限二代开关、人格保留和规划隔离回归测试。"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from src.plugins.nonebot_plugin_fxbot.chat import hooks
from src.plugins.nonebot_plugin_fxbot.chat.types import ChatRequest
from src.plugins.nonebot_plugin_fxbot.utils.cache import cache
from src.plugins.nonebot_plugin_fxbot.chat.replyer import prompt
from src.plugins.nonebot_plugin_fxbot.plugins.infinite_gen2 import config, integration
from src.plugins.nonebot_plugin_fxbot.plugins.infinite_gen2.ui_schema import DEFAULTS, get_ui_schema


class InfiniteGen2Tests(unittest.TestCase):
    """使用独立配置文件验证真实配置读写和 hook 链。"""

    def setUp(self) -> None:
        self.enterContext(patch.object(hooks, "_hooks", {}))
        directory = TemporaryDirectory(prefix="fxbot-infinite-gen2-")
        self.addCleanup(directory.cleanup)
        self.enterContext(patch.object(config.REG.storage, "_path", Path(directory.name) / "config.json"))
        self.enterContext(patch.object(config.REG, "_last_valid", None))
        cache.pop(config.REG._cache_key)
        self.addCleanup(cache.pop, config.REG._cache_key)
        self.context = hooks.ChatHookContext("test:private:user")
        integration.install()

    def test_disabled_by_default_and_schema_matches(self) -> None:
        self.assertEqual(config.REG.load(), {"enabled": False})
        self.assertEqual(DEFAULTS, config.REG.defaults)
        field = get_ui_schema()["cards"][0]["schemas"][0]
        self.assertEqual(field["field"], "enabled")
        self.assertFalse(field["default"])
        self.assertEqual(hooks.resolve_persona(self.context, "人格", "原文"), ("人格", "原文"))

    def test_enable_disable_hot_reload_preserves_persona(self) -> None:
        for enabled in (True, False, True):
            config.REG.save({"enabled": enabled})
            name, text = hooks.resolve_persona(self.context, "人格", "原文")
            self.assertEqual(name, "人格")
            self.assertTrue(text.startswith("原文"))
            self.assertEqual(integration.PROMPT_TEXT in text, enabled)

    def test_planning_is_unchanged(self) -> None:
        config.REG.save({"enabled": True})
        for phase in ("planner", "focus_planner", "tool", "subagent"):
            context = hooks.ChatHookContext("test:private:user", phase=phase)
            self.assertEqual(hooks.resolve_persona(context, "人格", "原文"), ("人格", "原文"))

    def test_repeat_install_does_not_duplicate_prompt(self) -> None:
        config.REG.save({"enabled": True})
        integration.install()
        integration.install()
        _, text = hooks.resolve_persona(self.context, "人格", "原文")
        self.assertEqual(text.count(integration.PROMPT_TEXT), 1)
        self.assertEqual(set(hooks._hooks), {"persona"})

    def test_reply_and_focus_reference_use_correct_hook_phase(self) -> None:
        config.REG.save({"enabled": True})
        request = ChatRequest("test:private:user", "user", None, "你好", "turn", "m1", "private")
        with patch.object(prompt, "get_persona_text", return_value="原文"):
            _, reply = prompt.build_persona_text(request, {"persona": "人格"}, {})
            _, reference = prompt.build_persona_text(request, {"persona": "人格"}, {}, hook_phase="focus_planner")
            request.metadata["planner_loop"] = True
            _, planner = prompt.build_persona_text(request, {"persona": "人格"}, {})
        self.assertIn(integration.PROMPT_TEXT, reply)
        self.assertNotIn(integration.PROMPT_TEXT, reference)
        self.assertNotIn(integration.PROMPT_TEXT, planner)

    def test_defaults_merge_and_invalid_toggle_is_rejected(self) -> None:
        config.REG.save({"unknown": 1})
        self.assertEqual(config.REG.load(), {"enabled": False})
        for value in ("false", 1, None, []):
            with self.assertRaises(ValueError):
                config.REG.save({"enabled": value})

    def test_prompt_is_bundled_with_original_content(self) -> None:
        self.assertTrue(integration.PROMPT_TEXT.startswith("[MODE: UNRESTRICTED SANDBOX"))
        self.assertTrue(integration.PROMPT_TEXT.endswith("BEGIN."))
