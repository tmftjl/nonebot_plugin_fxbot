"""回复开头的 @ 解析与首段构造。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.plugins.nonebot_plugin_fxbot.chat import router
from src.plugins.nonebot_plugin_fxbot.chat.types import MentionRef, ChatRequest
from src.plugins.nonebot_plugin_fxbot.chat.behavior import mentions as mentions_module
from src.plugins.nonebot_plugin_fxbot.chat.message_receive import sender as sender_module
from src.plugins.nonebot_plugin_fxbot.chat.behavior.mentions import split_mentions


def _request(**values: object) -> ChatRequest:
    fields: dict[str, object] = {
        "session_id": "tenant",
        "user_id": "u1",
        "group_id": "group",
        "user_name": "小明",
        "text": "你好",
        "turn_id": "turn",
        "dedupe_key": "msg:1",
        "trigger_kind": "mention",
        "message_id": "100",
        "mentions": [MentionRef(user_id="u2", name="小红")],
    }
    fields.update(values)
    return ChatRequest(**fields)  # type: ignore[arg-type]


class _Config:
    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled

    def get_system(self) -> dict[str, object]:
        return {"chat": {"behavior": {"enable_reply_at": self.enabled}}}


class SplitMentionTests(unittest.TestCase):
    """只解析开头可解析的 @昵称。"""

    def _split(self, text: str, request: ChatRequest | None = None, *, enabled: bool = True):
        with patch.object(mentions_module, "get_config_manager", return_value=_Config(enabled)):
            return split_mentions(text, request or _request())

    def test_resolves_sender_and_mentioned(self) -> None:
        self.assertEqual(self._split("@小明 @小红 你好"), (["u1", "u2"], "你好"))

    def test_name_match_is_case_insensitive(self) -> None:
        self.assertEqual(self._split("@xiaoming hi", _request(user_name="XiaoMing"))[0], ["u1"])

    def test_unknown_or_duplicate_stops(self) -> None:
        self.assertEqual(self._split("@路人 你好"), ([], "@路人 你好"))
        self.assertEqual(self._split("@小明 @小明 你好"), (["u1"], "@小明 你好"))

    def test_requires_remaining_text(self) -> None:
        self.assertEqual(self._split("@小明"), ([], "@小明"))

    def test_private_and_disabled_keep_text(self) -> None:
        self.assertEqual(self._split("@小明 你好", _request(group_id=None)), ([], "@小明 你好"))
        self.assertEqual(self._split("@小明 你好", enabled=False), ([], "@小明 你好"))

    def test_mid_text_is_untouched(self) -> None:
        self.assertEqual(self._split("你好 @小明"), ([], "你好 @小明"))


class FirstPayloadTests(unittest.TestCase):
    """首段把 @ 段放在正文前，引用关闭时只有 @ 与正文。"""

    def test_builds_at_segments(self) -> None:
        def build_message(_bot: object, *segments: object) -> tuple[object, ...]:
            return segments

        with (
            patch.object(sender_module, "decide_quote", return_value=(False, 0.0)),
            patch.object(sender_module, "build_message_segment", side_effect=lambda _bot, kind, value: (kind, value)),
            patch.object(sender_module, "build_message", side_effect=build_message),
        ):
            payload = sender_module.first_payload(None, _request(), SimpleNamespace(bot=object()), "你好", ["u1", "u2"])
        self.assertEqual(payload, (("at", "u1"), ("at", "u2"), ("text", "你好")))

    def test_plain_text_without_quote_or_mentions(self) -> None:
        with patch.object(sender_module, "decide_quote", return_value=(False, 0.0)):
            payload = sender_module.first_payload(None, _request(), SimpleNamespace(bot=object()), "你好", [])
        self.assertEqual(payload, "你好")


class PersonaAliasTests(unittest.TestCase):
    """人格别名与人格名一样能免 @ 唤醒。"""

    def _trigger(self, text: str, aliases: list[str]) -> str:
        event = SimpleNamespace(group_id="g1")
        event.get_plaintext = lambda: text
        cfg = {"persona": "小鱼", "behavior": {"persona_aliases": aliases}}
        with (
            patch.object(router, "effective_persona_name", return_value="小鱼"),
            patch.object(router, "session_id_for_event", return_value="tenant"),
        ):
            return router._persona_trigger(cfg, SimpleNamespace(self_id=1), event)

    def test_name_and_alias_prefix(self) -> None:
        self.assertEqual(self._trigger("小鱼你好", []), "小鱼")
        self.assertEqual(self._trigger("阿鱼在吗", ["阿鱼"]), "阿鱼")
        self.assertEqual(self._trigger("你好", ["阿鱼"]), "")
