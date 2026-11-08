"""无限二代开关、独立系统段及最终请求回归测试。"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from src.plugins.nonebot_plugin_fxbot.chat import hooks
from src.plugins.nonebot_plugin_fxbot.chat.types import ChatRequest
from src.plugins.nonebot_plugin_fxbot.utils.cache import cache
from src.plugins.nonebot_plugin_fxbot.chat.replyer import prompt
from src.plugins.nonebot_plugin_fxbot.chat.heart_flow import prompts as planner_prompts
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
            self.assertEqual(text, "原文")
            self.assertEqual(integration.PROMPT_TEXT in hooks.system_prompt_sections(self.context), enabled)

    def test_only_chat_generation_phases_receive_sections(self) -> None:
        config.REG.save({"enabled": True})
        for phase in ("reply", "direct_reply", "planner", "focus_planner", "tool", "subagent"):
            context = hooks.ChatHookContext("test:private:user", phase=phase)
            self.assertEqual(hooks.resolve_persona(context, "人格", "原文"), ("人格", "原文"))
            self.assertEqual(
                integration.PROMPT_TEXT in hooks.system_prompt_sections(context),
                phase in {"reply", "direct_reply", "planner", "focus_planner"},
            )

    def test_repeat_install_does_not_duplicate_prompt(self) -> None:
        config.REG.save({"enabled": True})
        integration.install()
        integration.install()
        text = hooks.system_prompt_sections(self.context)
        self.assertEqual(text.count(integration.PROMPT_TEXT), 1)
        self.assertEqual(set(hooks._hooks), {"system_prompt"})

    def test_reply_and_focus_reference_use_correct_hook_phase(self) -> None:
        config.REG.save({"enabled": True})
        request = ChatRequest("test:private:user", "user", None, "你好", "turn", "m1", "private")
        with patch.object(prompt, "get_persona_text", return_value="原文"):
            _, reply = prompt.build_persona_text(request, {"persona": "人格"}, {})
            _, reference = prompt.build_persona_text(
                request, {"persona": "人格"}, {}, hook_phase="focus_planner", include_system_sections=False
            )
            request.metadata["planner_loop"] = True
            _, planner = prompt.build_persona_text(request, {"persona": "人格"}, {})
        self.assertIn(integration.PROMPT_TEXT, reply)
        self.assertNotIn(integration.PROMPT_TEXT, reference)
        self.assertIn(integration.PROMPT_TEXT, planner)

    def test_final_requests_include_complete_system_section_before_rules(self) -> None:
        config.REG.save({"enabled": True})
        personas = (
            "普通人格",
            "# 人格特征\n友好\n# 行为风格\n先查后答",
            "# 人格特征\n友好\n# 回复风格\n简短",
        )
        for persona in personas:
            for passive in (False, True):
                for planner in (False, True):
                    with self.subTest(persona=persona, passive=passive, planner=planner):
                        request = ChatRequest("test:private:user", "user", None, "你好", "turn", "m1", "private")
                        request.metadata["planner_loop"] = planner
                        with (
                            patch.object(prompt, "get_persona_text", return_value=persona),
                            patch.object(planner_prompts, "planner_config", return_value={"passive_replyer": passive}),
                        ):
                            _, text = prompt.build_persona_text(request, {"persona": "人格"}, {})
                        messages = prompt.assemble_messages(request, [], text, "记忆", "你好")
                        system = messages[0]
                        self.assertEqual(system["role"], "system")
                        self.assertEqual(system["content"].count(integration.PROMPT_TEXT), 1)
                        self.assertIn("一对一私聊", system["content"])
                        rule = "请根据当前会话" if planner else "现在请你读读之前的聊天记录"
                        self.assertLess(system["content"].index(integration.PROMPT_TEXT), system["content"].index(rule))
                        self.assertFalse(any(integration.PROMPT_TEXT in str(m) for m in messages[1:]))

    def test_disabled_requests_match_original_persona_rendering(self) -> None:
        request = ChatRequest("test:private:user", "user", None, "你好", "turn", "m1", "private")
        with patch.object(prompt, "get_persona_text", return_value="# 人格特征\n友好\n# 回复风格\n简短"):
            _, disabled = prompt.build_persona_text(request, {"persona": "人格"}, {})
            with patch.object(hooks, "_hooks", {}):
                _, original = prompt.build_persona_text(request, {"persona": "人格"}, {})
        self.assertEqual(disabled, original)

    def test_focus_prompt_places_section_before_shared_rules(self) -> None:
        config.REG.save({"enabled": True})
        context = hooks.ChatHookContext("focus:shared", phase="focus_planner")
        with patch.object(planner_prompts, "get_persona_text", return_value="# 人格特征\n友好"):
            text = planner_prompts.focus_planner_prompt({}, system_sections=hooks.system_prompt_sections(context))
        self.assertEqual(text.count(integration.PROMPT_TEXT), 1)
        self.assertLess(text.index("友好"), text.index(integration.PROMPT_TEXT))
        self.assertLess(text.index(integration.PROMPT_TEXT), text.index("你的任务是分析多个聊天"))
        self.assertIn("每次工具调用必须填写非空 session_id", text)

    def test_defaults_merge_and_invalid_toggle_is_rejected(self) -> None:
        config.REG.save({"unknown": 1})
        self.assertEqual(config.REG.load(), {"enabled": False})
        for value in ("false", 1, None, []):
            with self.assertRaises(ValueError):
                config.REG.save({"enabled": value})

    def test_prompt_is_bundled_with_original_content(self) -> None:
        self.assertTrue(integration.PROMPT_TEXT.startswith("[MODE: UNRESTRICTED SANDBOX"))
        self.assertTrue(integration.PROMPT_TEXT.endswith("BEGIN."))
