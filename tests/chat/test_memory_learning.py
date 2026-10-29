"""学习目标匹配、共享组解析与配置校验。"""

from __future__ import annotations

import unittest

from src.plugins.nonebot_plugin_fxbot.chat.memory.learning import (
    TenantTarget,
    share_targets,
    share_tenants,
    tenant_target,
    learning_flags,
    target_matches,
    find_learning_item,
)
from src.plugins.nonebot_plugin_fxbot.config.system_defaults import SYSTEM_DEFAULTS, validate_system_config

GROUP_TARGET = TenantTarget(platform="onebot11", item_id="100", is_group=True, namespace="onebot11:bot")


class TenantTargetTests(unittest.TestCase):
    """租户串解析出平台、聊天类型与目标 ID。"""

    def test_group_and_private(self) -> None:
        group = tenant_target("onebot11:123:group:456")
        self.assertEqual((group.platform, group.item_id, group.is_group), ("onebot11", "456", True))
        private = tenant_target("qqofficial:123:private:789")
        self.assertEqual((private.platform, private.item_id, private.is_group), ("qqofficial", "789", False))

    def test_malformed_returns_none(self) -> None:
        for tenant in ("", "test:abc", "onebot11:123:channel:456", "onebot11:123:group:"):
            with self.subTest(tenant=tenant):
                self.assertIsNone(tenant_target(tenant))


class LearningItemTests(unittest.TestCase):
    """指定聊天 > 平台默认 > 全局默认，且只命中同类型聊天。"""

    def test_specificity(self) -> None:
        exact = {"platform": "onebot11", "item_id": "100", "type": "group", "use": False}
        any_platform = {"platform": "", "item_id": "100", "type": "group", "use": True}
        platform_default = {"platform": "onebot11", "item_id": "", "type": "group", "learn": False}
        other_type = {"platform": "onebot11", "item_id": "100", "type": "private", "use": False}
        global_default = {"platform": "", "item_id": "", "type": "group", "use": False}

        self.assertIs(
            find_learning_item([global_default, other_type, platform_default, any_platform, exact], GROUP_TARGET),
            exact,
        )
        self.assertIs(find_learning_item([global_default, platform_default, any_platform], GROUP_TARGET), any_platform)
        self.assertIs(find_learning_item([global_default, platform_default], GROUP_TARGET), platform_default)
        self.assertIs(find_learning_item([global_default], GROUP_TARGET), global_default)

    def test_wildcard_equals_empty(self) -> None:
        wildcard = {"platform": "*", "item_id": "*", "type": "group", "use": False}
        self.assertIs(find_learning_item([wildcard], GROUP_TARGET), wildcard)

    def test_flags_and_defaults(self) -> None:
        item = {"platform": "onebot11", "item_id": "100", "type": "group", "use": False, "learn": True}
        self.assertEqual(learning_flags([item], GROUP_TARGET), (False, True))
        self.assertEqual(learning_flags([], GROUP_TARGET), (True, True))
        self.assertEqual(learning_flags([item], None), (True, True))


class ShareGroupTests(unittest.TestCase):
    """共享组目标必须写全，并按平台、聊天类型与 ID 通配筛选租户。"""

    def test_target_matches(self) -> None:
        self.assertTrue(target_matches({"platform": "onebot11", "item_id": "100", "type": "group"}, GROUP_TARGET))
        self.assertTrue(target_matches({"platform": "*", "item_id": "*", "type": "group"}, GROUP_TARGET))
        self.assertFalse(target_matches({"platform": "onebot11", "item_id": "100", "type": "private"}, GROUP_TARGET))
        self.assertFalse(target_matches({"platform": "", "item_id": "100", "type": "group"}, GROUP_TARGET))

    def test_share_targets_returns_whole_group(self) -> None:
        first = {"platform": "onebot11", "item_id": "100", "type": "group"}
        second = {"platform": "onebot11", "item_id": "200", "type": "group"}
        unrelated = {"platform": "onebot11", "item_id": "300", "type": "group"}
        groups = [{"targets": [first, second]}, {"targets": [unrelated]}]

        self.assertEqual(share_targets(groups, GROUP_TARGET), [first, second])
        self.assertEqual(share_targets([], GROUP_TARGET), [])

    def test_share_tenants_filters_known(self) -> None:
        known = [
            "onebot11:bot:group:100",
            "onebot11:other:group:200",
            "onebot11:other:private:200",
            "qqofficial:other:group:100",
        ]
        targets = [{"platform": "onebot11", "item_id": "*", "type": "group"}]

        self.assertEqual(
            share_tenants("onebot11:bot:group:100", GROUP_TARGET, targets, known),
            ["onebot11:bot:group:100", "onebot11:other:group:200"],
        )
        exact = [{"platform": "onebot11", "item_id": "200", "type": "group"}]
        self.assertEqual(
            share_tenants("onebot11:bot:group:100", GROUP_TARGET, exact, known),
            ["onebot11:bot:group:100", "onebot11:other:group:200"],
        )


class LearningConfigTests(unittest.TestCase):
    """默认值与配置校验。"""

    def test_defaults(self) -> None:
        expression = SYSTEM_DEFAULTS["chat"]["expression"]
        self.assertEqual((expression["checked_only"], expression["self_reflect"]), (False, True))
        self.assertEqual(expression["learning_list"], [])
        self.assertEqual(expression["groups"], [])
        self.assertEqual(SYSTEM_DEFAULTS["chat"]["jargon"]["learning_list"], [])

    def test_valid_config(self) -> None:
        learning = [{"platform": "onebot11", "item_id": "100", "type": "group", "use": False, "learn": True}]
        groups = [{"targets": [{"platform": "onebot11", "item_id": "100", "type": "group"}]}]
        validate_system_config(
            {
                "chat": {
                    "expression": {"learning_list": learning, "groups": groups},
                    "jargon": {"learning_list": learning},
                }
            }
        )

    def test_invalid_config(self) -> None:
        for settings in (
            {"learning_list": "onebot11:100"},
            {"learning_list": [{"platform": 1}]},
            {"learning_list": [{"type": "channel"}]},
            {"learning_list": [{"platform": "", "item_id": "", "type": "group", "use": 1}]},
            {"groups": [{"targets": "onebot11:100"}]},
            {"groups": [{"targets": [{"platform": "", "item_id": "100", "type": "group"}]}]},
            {"groups": [{"targets": [{"platform": "onebot11", "item_id": "100"}]}]},
        ):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                validate_system_config({"chat": {"expression": settings}})
