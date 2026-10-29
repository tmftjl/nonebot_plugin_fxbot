"""配置校验与非法值回退测试。"""

from __future__ import annotations

import shutil
import unittest
from uuid import uuid4

from src.plugins.nonebot_plugin_fxbot.config import SYSTEM_DEFAULTS
from src.plugins.nonebot_plugin_fxbot.config.proxy import ConfigProxy
from src.plugins.nonebot_plugin_fxbot.config.system_defaults import validate_system_config


class SystemConfigValidationTests(unittest.TestCase):
    """系统配置的显式 schema 与回退行为。"""

    def setUp(self) -> None:
        self.proxy = ConfigProxy(
            namespace=f"test-{uuid4().hex}",
            defaults=SYSTEM_DEFAULTS,
            validator=validate_system_config,
        )
        self.addCleanup(shutil.rmtree, self.proxy.path.parent, True)

    def test_save_rejects_invalid_values(self) -> None:
        payloads = (
            {"chat": {"max_context_size": -1}},
            {"chat": {"max_context_size": "10"}},
            {"chat": {"max_context_size": True}},
            {"chat": {"max_private_context_size": -1}},
            {"chat": {"jargon": None}},
            {"chat": {"expression": None}},
            {"chat": {"expression": {"checked_only": "yes"}}},
            {"chat": {"expression": {"max_expression_learner": 0}}},
            {"chat": {"expression": {"max_expression_learner": 21}}},
            {"chat": {"expression": {"max_expression_learner": True}}},
            {"chat": {"planner": None}},
            {"chat": {"planner": {"mode": "smart"}}},
            {"chat": {"planner": {"wait_seconds": 29}}},
            {"chat": {"behavior": None}},
            {"chat": {"behavior": {"enable_reply_at": 1}}},
            {"chat": {"behavior": {"group_quote_frequency": "sometimes"}}},
            {"chat": {"behavior": {"persona_aliases": "阿鱼"}}},
            {"chat": {"behavior": {"private_quote_frequency": "always"}}},
            {"chat": {"behavior": {"group_chat_prompt": []}}},
            {"chat": {"behavior": {"private_chat_prompts": 1}}},
        )
        for payload in payloads:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.proxy.save(payload)

    def test_invalid_file_falls_back_to_defaults(self) -> None:
        self.proxy.storage.write({"chat": {"max_context_size": -5, "expression": None}})

        data = self.proxy.load()

        self.assertEqual(data["chat"]["max_context_size"], SYSTEM_DEFAULTS["chat"]["max_context_size"])
        self.assertEqual(data["chat"]["expression"], SYSTEM_DEFAULTS["chat"]["expression"])
        self.assertEqual(self.proxy.storage.read()["chat"]["max_context_size"], -5)

    def test_merge_and_save_does_not_crash_on_invalid_file(self) -> None:
        self.proxy.storage.write({"chat": {"expression": None}})

        merged = self.proxy.merge_and_save()

        self.assertEqual(merged["chat"]["expression"], SYSTEM_DEFAULTS["chat"]["expression"])
        self.assertIsNone(self.proxy.storage.read()["chat"]["expression"])
        self.assertEqual(self.proxy.load()["chat"]["expression"], SYSTEM_DEFAULTS["chat"]["expression"])

    def test_partial_config_is_merged_and_valid(self) -> None:
        self.proxy.storage.write(
            {
                "chat": {
                    "max_context_size": 0,
                    "jargon": {"learning_list": []},
                    "expression": {"checked_only": True},
                    "planner": {"mode": "complex"},
                }
            }
        )

        data = self.proxy.load()

        self.assertEqual(data["chat"]["max_context_size"], 0)
        self.assertEqual(data["chat"]["jargon"], {"learning_list": [], "groups": []})
        self.assertEqual(
            data["chat"]["expression"],
            {
                "checked_only": True,
                "self_reflect": True,
                "max_expression_learner": 3,
                "learning_list": [],
                "groups": [],
                "vector": {
                    "enabled": False,
                    "index_path": "data/expression_selection/expression_vector_index.json",
                    "candidate_pool_size": 50,
                },
            },
        )
        self.assertEqual(
            data["chat"]["planner"],
            {
                "mode": "complex",
                "wait_seconds": 60,
                "context_summary_max_chars": 6000,
                "passive_replyer": False,
            },
        )
