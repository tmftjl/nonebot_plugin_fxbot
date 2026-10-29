"""临时表达风格、额外 Prompt 与注意力漂移。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from src.plugins.nonebot_plugin_fxbot.chat.types import ChatRequest
from src.plugins.nonebot_plugin_fxbot.chat.replyer import prompt
from src.plugins.nonebot_plugin_fxbot.config.system_defaults import SYSTEM_DEFAULTS, validate_system_config


def _request(*, group: bool = True, platform: str = "OneBot V11") -> ChatRequest:
    return ChatRequest(
        session_id=f"{platform}:bot:group:123" if group else f"{platform}:bot:private:u1",
        user_id="u1",
        group_id="123" if group else None,
        text="你好",
        turn_id="turn",
        dedupe_key="msg:1",
        trigger_kind="mention",
        platform=platform,
    )


def _behavior(**values: object) -> dict[str, object]:
    settings: dict[str, object] = {
        "multiple_reply_style": ["很短", "带点翻译腔"],
        "multiple_probability": 0.0,
        "chat_prompts": [],
    }
    settings.update(values)
    return settings


class TemporaryStyleTests(unittest.TestCase):
    """备用风格按概率抽取。"""

    def test_zero_probability(self) -> None:
        self.assertEqual(prompt.temporary_style_note(_behavior()), "")

    def test_always_probability(self) -> None:
        note = prompt.temporary_style_note(_behavior(multiple_probability=1.0))
        self.assertTrue(any(note == f"你的说话风格可以尝试：\n{style}" for style in ["很短", "带点翻译腔"]))

    def test_empty_styles(self) -> None:
        settings = _behavior(multiple_reply_style=[], multiple_probability=1)
        self.assertEqual(prompt.temporary_style_note(settings), "")

    def test_no_draw_when_random_exceeds_probability(self) -> None:
        with patch.object(prompt.random, "random", return_value=0.8):
            self.assertEqual(prompt.temporary_style_note(_behavior(multiple_probability=0.5)), "")


class ExtraPromptTests(unittest.TestCase):
    """额外 Prompt 按平台与目标 ID 匹配。"""

    def _cfg(self, *entries: str) -> dict[str, object]:
        return {"behavior": _behavior(chat_prompts=list(entries))}

    def test_group_match(self) -> None:
        cfg = self._cfg("OneBot V11:123:group:群里要短")
        self.assertEqual(prompt.extra_prompt_block(cfg, _request()), "群里要短")

    def test_group_mismatch(self) -> None:
        for entry, request in (
            ("QQ:123:group:群里要短", _request()),
            ("OneBot V11:456:group:群里要短", _request()),
            ("OneBot V11:u1:private:私聊要稳", _request()),
        ):
            with self.subTest(entry=entry):
                self.assertEqual(prompt.extra_prompt_block(self._cfg(entry), request), "")

    def test_private_match(self) -> None:
        cfg = self._cfg("OneBot V11:u1:private:私聊要稳")
        self.assertEqual(prompt.extra_prompt_block(cfg, _request(group=False)), "私聊要稳")

    def test_multiple_entries_joined(self) -> None:
        cfg = self._cfg("OneBot V11:123:group:第一条", "OneBot V11:123:group:第二条带:冒号")
        self.assertEqual(prompt.extra_prompt_block(cfg, _request()), "第一条\n第二条带:冒号")

    def test_malformed_entry_ignored(self) -> None:
        cfg = self._cfg("OneBot V11:123:group")
        self.assertEqual(prompt.extra_prompt_block(cfg, _request()), "")


class AttentionDriftTests(unittest.TestCase):
    """漂移提示块只在启用时注入。"""

    def test_disabled(self) -> None:
        self.assertEqual(prompt.attention_drift_block({"enabled": False}), "")
        self.assertEqual(prompt.attention_drift_block(None), "")

    def test_selected_rules(self) -> None:
        block = prompt.attention_drift_block(
            {"enabled": True, "drift_level": "wild", "anchor_policy": "strict", "reaction_style": "reserved"}
        )
        self.assertIn(prompt._DRIFT_LEVEL_RULES["wild"], block)
        self.assertIn(prompt._ANCHOR_POLICY_RULES["strict"], block)
        self.assertIn(prompt._REACTION_STYLE_RULES["reserved"], block)

    def test_unknown_value_falls_back(self) -> None:
        block = prompt.attention_drift_block({"enabled": True, "drift_level": "nope"})
        self.assertIn(prompt._DRIFT_LEVEL_RULES["scattered"], block)


class ConfigTests(unittest.TestCase):
    """默认值与校验。"""

    def test_defaults(self) -> None:
        behavior = SYSTEM_DEFAULTS["chat"]["behavior"]
        self.assertEqual(behavior["multiple_probability"], 0.0)
        self.assertTrue(behavior["multiple_reply_style"])
        self.assertFalse(SYSTEM_DEFAULTS["chat"]["attention_drift"]["enabled"])

    def test_validation(self) -> None:
        validate_system_config(
            {
                "chat": {
                    "behavior": {
                        "multiple_reply_style": ["很短"],
                        "multiple_probability": 0.5,
                        "chat_prompts": ["OneBot V11:123:group:提示"],
                    },
                    "attention_drift": {
                        "enabled": True,
                        "drift_level": "wild",
                        "anchor_policy": "loose",
                        "reaction_style": "natural",
                    },
                }
            }
        )
        for data in (
            {"behavior": {"multiple_reply_style": "很短"}},
            {"behavior": {"multiple_probability": 2}},
            {"behavior": {"chat_prompts": ["OneBot V11:123:group"]}},
            {"behavior": {"chat_prompts": ["OneBot V11:123:other:提示"]}},
            {"attention_drift": {"enabled": "yes"}},
            {"attention_drift": {"drift_level": "nope"}},
        ):
            with self.subTest(data=data), self.assertRaises(ValueError):
                validate_system_config({"chat": data})


if __name__ == "__main__":
    unittest.main()
