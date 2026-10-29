"""回复时机：私聊暂停、@ 与提及必回复、防抖档位。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.plugins.nonebot_plugin_fxbot.chat import router
from src.plugins.nonebot_plugin_fxbot.config.system_defaults import SYSTEM_DEFAULTS, validate_system_config


def _bot() -> SimpleNamespace:
    return SimpleNamespace(self_id="bot", adapter=SimpleNamespace(get_name=lambda: "test"))


def _event(*, group_id: str | None = "g1", user_id: str = "u1") -> SimpleNamespace:
    return SimpleNamespace(user_id=user_id, group_id=group_id, message=[], text="你好")


def _cfg(**timing: object) -> dict[str, object]:
    values: dict[str, object] = {
        "message_debounce_mode": "natural",
        "private_reply_paused": False,
        "private_reply_pause_rules": [],
        "mentioned_bot_reply": True,
        "inevitable_at_reply": True,
    }
    values.update(timing)
    return {
        "enabled": True,
        "max_context_size": 10,
        "group_allowlist": [],
        "private_enabled": True,
        "private_allowlist": [],
        "reply_timing": values,
    }


class PrivatePauseTests(unittest.TestCase):
    """全局暂停与单用户规则。"""

    def test_global_pause(self) -> None:
        cfg = _cfg(private_reply_paused=True)
        self.assertTrue(router._private_paused(cfg, _bot(), _event(group_id=None)))

    def test_rule_overrides_global(self) -> None:
        cfg = _cfg(
            private_reply_paused=True,
            private_reply_pause_rules=[{"platform": "test", "user_id": "u1", "paused": False}],
        )
        self.assertFalse(router._private_paused(cfg, _bot(), _event(group_id=None)))

    def test_rule_matches_user_and_platform(self) -> None:
        rules = [
            {"platform": "other", "user_id": "u1", "paused": True},
            {"platform": "test", "user_id": "u1", "paused": True},
        ]
        self.assertTrue(router._private_paused(_cfg(private_reply_pause_rules=rules), _bot(), _event(group_id=None)))
        self.assertFalse(
            router._private_paused(_cfg(private_reply_pause_rules=rules), _bot(), _event(group_id=None, user_id="u2"))
        )


class MatchContextTests(unittest.TestCase):
    """必回复开关与暂停私聊的路由结果。"""

    def test_at_mention_toggle(self) -> None:
        with (
            patch.object(router, "_is_to_me", return_value=True),
            patch.object(router, "_persona_trigger", return_value=""),
        ):
            self.assertTrue(router._match_context(_cfg(), _bot(), _event()))
            self.assertFalse(router._match_context(_cfg(inevitable_at_reply=False), _bot(), _event()))

    def test_persona_mention_toggle(self) -> None:
        with (
            patch.object(router, "_is_to_me", return_value=False),
            patch.object(router, "_persona_trigger", return_value="小鱼"),
        ):
            self.assertTrue(router._match_context(_cfg(), _bot(), _event()))
            self.assertFalse(router._match_context(_cfg(mentioned_bot_reply=False), _bot(), _event()))

    def test_paused_private_does_not_reach_ai(self) -> None:
        self.assertTrue(router._match_context(_cfg(), _bot(), _event(group_id=None)))
        self.assertFalse(router._match_context(_cfg(private_reply_paused=True), _bot(), _event(group_id=None)))

    def test_context_record_covers_disabled_forcing(self) -> None:
        with (
            patch.object(router, "_is_to_me", return_value=True),
            patch.object(router, "_persona_trigger", return_value=""),
        ):
            self.assertFalse(router._should_record_context(_cfg(), _bot(), _event()))
            self.assertTrue(router._should_record_context(_cfg(inevitable_at_reply=False), _bot(), _event()))
        self.assertTrue(router._should_record_context(_cfg(private_reply_paused=True), _bot(), _event(group_id=None)))
        self.assertFalse(router._should_record_context(_cfg(), _bot(), _event(group_id=None)))


class ReplyTimingConfigTests(unittest.TestCase):
    """默认值与校验。"""

    def test_defaults(self) -> None:
        timing = SYSTEM_DEFAULTS["chat"]["reply_timing"]
        self.assertEqual(timing["message_debounce_mode"], "natural")
        self.assertFalse(timing["private_reply_paused"])
        self.assertTrue(timing["mentioned_bot_reply"])
        self.assertTrue(timing["inevitable_at_reply"])

    def test_validation(self) -> None:
        validate_system_config(
            {
                "chat": {
                    "reply_timing": {
                        "message_debounce_mode": "steady",
                        "private_reply_paused": True,
                        "private_reply_pause_rules": [{"platform": "", "user_id": "u1", "paused": True}],
                        "mentioned_bot_reply": False,
                        "inevitable_at_reply": False,
                    }
                }
            }
        )
        for timing in (
            {"message_debounce_mode": "slow"},
            {"private_reply_paused": 1},
            {"mentioned_bot_reply": "yes"},
            {"private_reply_pause_rules": [{"user_id": ""}]},
            {"private_reply_pause_rules": [{"user_id": "u1", "paused": "yes"}]},
        ):
            with self.subTest(timing=timing), self.assertRaises(ValueError):
                validate_system_config({"chat": {"reply_timing": timing}})
