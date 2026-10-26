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
            {"chat": {"max_history": -1}},
            {"chat": {"max_history": "10"}},
            {"chat": {"max_history": True}},
            {"chat": {"memory": None}},
            {"chat": {"memory": False}},
            {"chat": {"memory": {"history_enabled": 1}}},
            {"chat": {"jargon": None}},
            {"chat": {"jargon": {"enabled": "yes"}}},
            {"chat": {"jargon": {"inject_max": -1}}},
            {"chat": {"jargon": {"inject_max": 21}}},
            {"chat": {"expression": None}},
            {"chat": {"expression": {"enabled": "yes"}}},
            {"chat": {"expression": {"sample_size": 0}}},
            {"chat": {"expression": {"inject_top_k": 21}}},
        )
        for payload in payloads:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.proxy.save(payload)

    def test_invalid_file_falls_back_to_defaults(self) -> None:
        self.proxy.storage.write({"chat": {"max_history": -5, "memory": None}})

        data = self.proxy.load()

        self.assertEqual(data["chat"]["max_history"], SYSTEM_DEFAULTS["chat"]["max_history"])
        self.assertEqual(data["chat"]["memory"], SYSTEM_DEFAULTS["chat"]["memory"])
        self.assertEqual(self.proxy.storage.read()["chat"]["max_history"], -5)

    def test_merge_and_save_does_not_crash_on_invalid_file(self) -> None:
        self.proxy.storage.write({"chat": {"memory": None}})

        merged = self.proxy.merge_and_save()

        self.assertEqual(merged["chat"]["memory"], SYSTEM_DEFAULTS["chat"]["memory"])
        self.assertIsNone(self.proxy.storage.read()["chat"]["memory"])
        self.assertEqual(self.proxy.load()["chat"]["memory"], SYSTEM_DEFAULTS["chat"]["memory"])

    def test_partial_config_is_merged_and_valid(self) -> None:
        self.proxy.storage.write(
            {
                "chat": {
                    "max_history": 0,
                    "memory": {"learn_enabled": True},
                    "jargon": {"inject_max": 3},
                    "expression": {"inject_top_k": 3},
                }
            }
        )

        data = self.proxy.load()

        self.assertEqual(data["chat"]["max_history"], 0)
        self.assertTrue(data["chat"]["memory"]["learn_enabled"])
        self.assertTrue(data["chat"]["memory"]["history_enabled"])
        self.assertEqual(data["chat"]["jargon"], {"enabled": True, "inject_max": 3})
        self.assertEqual(
            data["chat"]["expression"],
            {"enabled": True, "learn": True, "llm_select": False, "sample_size": 50, "inject_top_k": 3},
        )
