"""错字模拟、保护片段与发送历史测试。"""

from __future__ import annotations

import random
import unittest
from types import SimpleNamespace
from contextlib import ExitStack
from unittest.mock import patch

from src.plugins.nonebot_plugin_fxbot.chat import typo as typo_module, service as service_module
from src.plugins.nonebot_plugin_fxbot.chat.typo import apply_typo
from src.plugins.nonebot_plugin_fxbot.chat.tools import ToolRuntime
from src.plugins.nonebot_plugin_fxbot.chat.types import ChatRequest, ChatResponse
from src.plugins.nonebot_plugin_fxbot.chat.service import ChatService
from src.plugins.nonebot_plugin_fxbot.chat.session import ChatSessionStore
from src.plugins.nonebot_plugin_fxbot.config.system_defaults import (
    SYSTEM_DEFAULTS,
    validate_system_config,
)


class _Zero(random.Random):
    """始终命中概率分支，便于验证替换与自纠。"""

    def random(self) -> float:
        return 0.0


def _config(**values: object) -> dict[str, object]:
    """构造开启的错字配置。"""
    config: dict[str, object] = {
        "enabled": True,
        "error_rate": 0.0,
        "tone_error_rate": 0.0,
        "word_replace_rate": 0.0,
        "correction_quote": False,
    }
    config.update(values)
    return config


def _onebot() -> SimpleNamespace:
    """构造带 OneBot 标识的 Bot。"""
    return SimpleNamespace(adapter=SimpleNamespace(get_name=lambda: "OneBot V11"))


class TypoRulesTests(unittest.TestCase):
    """同音、声调、整词与保护片段。"""

    def test_disabled_returns_original(self) -> None:
        text = "今天天气真好"
        self.assertEqual(apply_typo(text, rng=_Zero(), config=_config(enabled=False)), text)

    def test_protected_spans_are_untouched(self) -> None:
        text = "看 https://a.com/天气 @小明 42 `代码` （好） 【标题】 #确认提醒"

        result = apply_typo(text, rng=_Zero(), config=_config(error_rate=1.0))

        for span in ("https://a.com/天气", "@小明", "42", "`代码`", "（好）", "【标题】", "#确认提醒"):
            self.assertIn(span, result)

    def test_word_replacement(self) -> None:
        result = apply_typo(
            "因为现在心情很好，名字也完成了",
            rng=random.Random(3),
            config=_config(word_replace_rate=1.0),
        )
        self.assertEqual(result, "因位现再新情很好，明字也玩成了")

    def test_same_base_replacement(self) -> None:
        result = apply_typo("妈麻马骂", rng=random.Random(7), config=_config(error_rate=1.0))

        self.assertEqual(len(result), 4)
        for source, target in zip("妈麻马骂", result):
            self.assertNotEqual(source, target)
            self.assertEqual(typo_module._read(source)[0], typo_module._read(target)[0])

    def test_tone_replacement_keeps_base(self) -> None:
        result = apply_typo(
            "妈麻马骂", rng=random.Random(7), config=_config(tone_error_rate=1.0)
        )

        for source, target in zip("妈麻马骂", result):
            self.assertNotEqual(source, target)
            source_base, source_tone = typo_module._read(source)
            target_base, target_tone = typo_module._read(target)
            self.assertEqual(source_base, target_base)
            self.assertNotEqual(source_tone, target_tone)

    def test_ascii_and_punctuation_untouched(self) -> None:
        result = apply_typo(
            "hello 2026, ok!", rng=_Zero(), config=_config(error_rate=1.0, tone_error_rate=1.0)
        )
        self.assertEqual(result, "hello 2026, ok!")

    def test_correction_quote_only_on_onebot(self) -> None:
        result = apply_typo("天气", bot=_onebot(), rng=_Zero(), config=_config(error_rate=1.0, correction_quote=True))
        self.assertTrue(result.endswith("（打错了，是 天）"))
        plain = apply_typo("天气", bot=None, rng=_Zero(), config=_config(error_rate=1.0, correction_quote=True))
        self.assertNotIn("打错了", plain)

    def test_correction_can_be_disabled(self) -> None:
        result = apply_typo(
            "天气",
            bot=_onebot(),
            rng=_Zero(),
            config=_config(error_rate=1.0, correction_quote=False),
        )
        self.assertNotIn("打错了", result)

    def test_no_mistake_has_no_quote(self) -> None:
        result = apply_typo(
            "hello",
            bot=_onebot(),
            rng=_Zero(),
            config=_config(error_rate=1.0, correction_quote=True),
        )
        self.assertEqual(result, "hello")

    def test_config_failure_returns_original(self) -> None:
        with patch.object(typo_module, "get_config_manager", side_effect=RuntimeError("bad")):
            self.assertEqual(apply_typo("天气", rng=_Zero()), "天气")


class TypoConfigTests(unittest.TestCase):
    """默认值与校验。"""

    def test_defaults_disabled(self) -> None:
        self.assertFalse(SYSTEM_DEFAULTS["chat"]["typo"]["enabled"])
        self.assertEqual(SYSTEM_DEFAULTS["chat"]["typo"]["error_rate"], 0.01)
        self.assertEqual(SYSTEM_DEFAULTS["chat"]["typo"]["tone_error_rate"], 0.1)
        self.assertEqual(SYSTEM_DEFAULTS["chat"]["typo"]["word_replace_rate"], 0.006)

    def test_validation(self) -> None:
        validate_system_config({"chat": {"typo": _config()}})
        with self.assertRaises(ValueError):
            validate_system_config({"chat": {"typo": {"enabled": "yes"}}})
        with self.assertRaises(ValueError):
            validate_system_config({"chat": {"typo": {"error_rate": 2}}})
        with self.assertRaises(ValueError):
            validate_system_config({"chat": {"typo": {"correction_quote": 1}}})


class _Config:
    """提供发送路径所需的最小配置。"""

    def get_system(self) -> dict[str, object]:
        return {"chat": {"max_history": 10, "behavior": {"quote_mode": "never"}}}


class _Matcher:
    """记录发送载荷。"""

    def __init__(self) -> None:
        self.sent: list[object] = []

    async def send(self, payload: object) -> None:
        self.sent.append(payload)


class SendTypoTests(unittest.IsolatedAsyncioTestCase):
    """发送文本改错字，历史仍写干净文本。"""

    async def test_send_uses_typo_but_history_is_clean(self) -> None:
        store = ChatSessionStore()
        request = ChatRequest(
            session_id="tenant",
            user_id="user",
            group_id="group",
            text="你好",
            turn_id="turn",
            dedupe_key="msg:1",
            trigger_kind="mention",
        )
        response = ChatResponse(text="干净文本。", epoch=0, metadata={"reply_parts": ["干净文本。"]})
        matcher = _Matcher()
        store.append("tenant", {"role": "user", "content": "你好"})

        with ExitStack() as stack:
            stack.enter_context(patch.object(service_module, "get_config_manager", return_value=_Config()))
            stack.enter_context(patch.object(service_module.memory_state, "is_ready", return_value=False))
            stack.enter_context(patch.object(service_module, "apply_typo", return_value="错字文本。"))
            sent = await ChatService(store)._send_parts(
                request, response, ToolRuntime(bot=object(), matcher=matcher), matcher
            )

        self.assertTrue(sent)
        self.assertEqual(matcher.sent, ["错字文本。"])
        self.assertEqual(
            store.get("tenant"),
            [
                {"role": "user", "content": "你好"},
                {"role": "assistant", "content": "干净文本。"},
            ],
        )
