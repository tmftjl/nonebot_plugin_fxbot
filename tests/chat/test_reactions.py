"""关键词与正则反应提示。"""

from __future__ import annotations

import unittest

from src.plugins.nonebot_plugin_fxbot.config.system_defaults import SYSTEM_DEFAULTS, validate_system_config
from src.plugins.nonebot_plugin_fxbot.chat.message_receive.reactions import reaction_block


def _cfg(**settings: object) -> dict[str, object]:
    return {"message_receive": settings}


class ReactionTests(unittest.TestCase):
    """命中规则后追加固定反应提示。"""

    def test_keyword_hit(self) -> None:
        block = reaction_block(_cfg(keyword_rules=[{"keywords": ["早上好"], "reaction": "回一句早安"}]), "大家早上好")
        self.assertIn("【关键词反应】", block)
        self.assertIn("- 回一句早安", block)

    def test_no_hit_or_empty_text(self) -> None:
        config = _cfg(keyword_rules=[{"keywords": ["早上好"], "reaction": "回一句早安"}])
        self.assertEqual(reaction_block(config, "晚安"), "")
        self.assertEqual(reaction_block(config, ""), "")
        self.assertEqual(reaction_block({}, "早上好"), "")

    def test_regex_named_group(self) -> None:
        rule = {"regex": [r"^(?P<name>\S+)在吗$"], "reaction": "回应 [name] 的呼叫"}
        self.assertIn("- 回应 小明 的呼叫", reaction_block(_cfg(regex_rules=[rule]), "小明在吗"))

    def test_bad_regex_is_ignored(self) -> None:
        rule = {"regex": ["("], "reaction": "不该命中"}
        self.assertEqual(reaction_block(_cfg(regex_rules=[rule]), "任何消息"), "")

    def test_multiple_rules_keep_order(self) -> None:
        config = _cfg(
            keyword_rules=[{"keywords": ["猫"], "reaction": "喜欢猫"}],
            regex_rules=[{"regex": [r"^喵"], "reaction": "学猫叫"}],
        )
        block = reaction_block(config, "喵，猫猫")
        self.assertLess(block.index("喜欢猫"), block.index("学猫叫"))


class ReactionConfigTests(unittest.TestCase):
    """默认值与校验。"""

    def test_defaults(self) -> None:
        settings = SYSTEM_DEFAULTS["chat"]["message_receive"]
        self.assertEqual(settings["keyword_rules"], [])
        self.assertEqual(settings["regex_rules"], [])

    def test_valid_rules(self) -> None:
        validate_system_config(
            {
                "chat": {
                    "message_receive": {
                        "keyword_rules": [{"keywords": ["猫"], "reaction": "喜欢猫"}],
                        "regex_rules": [{"regex": [r"^喵"], "reaction": "学猫叫"}],
                    }
                }
            }
        )

    def test_invalid_rules(self) -> None:
        for settings in (
            {"keyword_rules": ["猫"]},
            {"keyword_rules": [{"keywords": "猫", "reaction": "喜欢猫"}]},
            {"keyword_rules": [{"keywords": [], "regex": [], "reaction": "空规则"}]},
            {"keyword_rules": [{"keywords": ["猫"], "reaction": 1}]},
            {"regex_rules": [{"regex": ["("], "reaction": "坏正则"}]},
        ):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                validate_system_config({"chat": {"message_receive": settings}})
