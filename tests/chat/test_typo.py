"""错字模拟、保护片段、纠正消息与发送历史测试。"""

from __future__ import annotations

import random
import unittest
from types import SimpleNamespace
from contextlib import ExitStack
from unittest.mock import patch

from src.plugins.nonebot_plugin_fxbot.chat import typo as typo_module, service as service_module
from src.plugins.nonebot_plugin_fxbot.chat.typo import TypoText, apply_typo
from src.plugins.nonebot_plugin_fxbot.chat.tools import ToolRuntime
from src.plugins.nonebot_plugin_fxbot.chat.types import ChatRequest, ChatResponse
from src.plugins.nonebot_plugin_fxbot.chat.service import ChatService
from src.plugins.nonebot_plugin_fxbot.chat.session import ChatSessionStore
from src.plugins.nonebot_plugin_fxbot.chat.message_receive import sender as sender_module
from src.plugins.nonebot_plugin_fxbot.config.system_defaults import (
    SYSTEM_DEFAULTS,
    validate_system_config,
)


class _Zero(random.Random):
    """始终命中概率分支，便于验证替换与纠正。"""

    def random(self) -> float:
        return 0.0


class _Seq(random.Random):
    """按给定序列返回随机数，用完后返回 0.0。"""

    def __init__(self, *values: float) -> None:
        super().__init__()
        self._values = list(values)

    def random(self) -> float:
        return self._values.pop(0) if self._values else 0.0


def _forced_typo(
    text: str, _config: dict[str, object], _rng: random.Random, words: list[str], _chars: list[str]
) -> str:
    """固定产出一个错字句和一条词级纠正，便于单独验证两条 50% 分支。"""
    words.append("因为")
    return "错字"


def _config(**values: object) -> dict[str, object]:
    """构造开启的错字配置。"""
    config: dict[str, object] = {
        "enable": True,
        "error_rate": 0.0,
        "min_freq": 9,
        "tone_error_rate": 0.0,
        "word_replace_rate": 0.0,
        "enable_correction_quote": False,
    }
    config.update(values)
    return config


class TypoRulesTests(unittest.TestCase):
    """同音、声调、整词、保护片段与纠正内容。"""

    def test_disabled_returns_original(self) -> None:
        text = "今天天气真好"
        self.assertEqual(apply_typo(text, rng=_Zero(), config=_config(enable=False)), TypoText(text))

    def test_protected_spans_are_untouched(self) -> None:
        text = "看 https://a.com/天气 @小明 42 `代码` （好） 【标题】 #确认提醒"

        result = apply_typo(text, rng=_Zero(), config=_config(error_rate=1.0)).text

        for span in ("https://a.com/天气", "@小明", "42", "`代码`", "（好）", "【标题】", "#确认提醒"):
            self.assertIn(span, result)

    def test_word_replacement(self) -> None:
        result = apply_typo(
            "因为现在心情很好，名字也完成了",
            rng=_Zero(),
            config=_config(word_replace_rate=1.0),
        )
        self.assertEqual(result.text, "因位现再新情很好，明字也玩成了")

    def test_same_base_replacement(self) -> None:
        result = apply_typo("妈麻马骂", rng=_Zero(), config=_config(error_rate=1.0)).text

        self.assertEqual(len(result), 4)
        for source, target in zip("妈麻马骂", result):
            self.assertNotEqual(source, target)
            self.assertEqual(typo_module._read(source)[0], typo_module._read(target)[0])

    def test_tone_replacement_keeps_base(self) -> None:
        result = apply_typo("妈麻马骂", rng=_Zero(), config=_config(tone_error_rate=1.0)).text

        for source, target in zip("妈麻马骂", result):
            self.assertNotEqual(source, target)
            source_base, source_tone = typo_module._read(source)
            target_base, target_tone = typo_module._read(target)
            self.assertEqual(source_base, target_base)
            self.assertNotEqual(source_tone, target_tone)

    def test_ascii_and_punctuation_untouched(self) -> None:
        result = apply_typo("hello 2026, ok!", rng=_Zero(), config=_config(error_rate=1.0, tone_error_rate=1.0))
        self.assertEqual(result.text, "hello 2026, ok!")

    def test_correction_is_separate_from_text(self) -> None:
        result = apply_typo("妈妈", rng=_Zero(), config=_config(error_rate=1.0))

        self.assertNotEqual(result.text, "妈妈")
        self.assertNotIn("打错了", result.text)
        self.assertEqual(result.correction, "妈")

    def test_word_correction_preferred(self) -> None:
        result = apply_typo("因为妈妈", rng=_Zero(), config=_config(error_rate=1.0, word_replace_rate=1.0))
        self.assertEqual(result.correction, "因为")

    def test_half_chance_sends_clean_sentence(self) -> None:
        # 纠正抽签、选词、是否发送错字句的抽签
        with patch.object(typo_module, "_sentence_typo", new=_forced_typo):
            result = apply_typo("原句", rng=_Seq(0.0, 0.0, 0.9), config=_config())
        self.assertEqual(result, TypoText("原句"))

    def test_no_correction_when_coin_fails(self) -> None:
        with patch.object(typo_module, "_sentence_typo", new=_forced_typo):
            result = apply_typo("原句", rng=_Seq(0.9), config=_config())
        self.assertEqual(result, TypoText("错字"))

    def test_min_freq_blocks_rare_candidates(self) -> None:
        self.assertEqual(apply_typo("天", rng=_Zero(), config=_config(error_rate=1.0)).text, "天")
        self.assertNotEqual(apply_typo("天", rng=_Zero(), config=_config(error_rate=1.0, min_freq=0)).text, "天")

    def test_frequency_table_covers_pool(self) -> None:
        self.assertEqual(set(typo_module._FREQ), set(typo_module._POOL))

    def test_no_mistake_has_no_correction(self) -> None:
        self.assertEqual(apply_typo("hello", rng=_Zero(), config=_config(error_rate=1.0)), TypoText("hello"))

    def test_config_failure_returns_original(self) -> None:
        with patch.object(typo_module, "get_config_manager", side_effect=RuntimeError("bad")):
            self.assertEqual(apply_typo("天气", rng=_Zero()), TypoText("天气"))


class TypoConfigTests(unittest.TestCase):
    """默认值与校验。"""

    def test_defaults(self) -> None:
        self.assertTrue(SYSTEM_DEFAULTS["chat"]["typo"]["enable"])
        self.assertEqual(SYSTEM_DEFAULTS["chat"]["typo"]["error_rate"], 0.01)
        self.assertEqual(SYSTEM_DEFAULTS["chat"]["typo"]["min_freq"], 9)
        self.assertEqual(SYSTEM_DEFAULTS["chat"]["typo"]["tone_error_rate"], 0.1)
        self.assertEqual(SYSTEM_DEFAULTS["chat"]["typo"]["word_replace_rate"], 0.006)
        self.assertEqual(SYSTEM_DEFAULTS["chat"]["typo"]["correction_quote_probability"], 1.0)

    def test_validation(self) -> None:
        validate_system_config({"chat": {"typo": _config()}})
        with self.assertRaises(ValueError):
            validate_system_config({"chat": {"typo": {"enable": "yes"}}})
        with self.assertRaises(ValueError):
            validate_system_config({"chat": {"typo": {"error_rate": 2}}})
        with self.assertRaises(ValueError):
            validate_system_config({"chat": {"typo": {"min_freq": "9"}}})
        with self.assertRaises(ValueError):
            validate_system_config({"chat": {"typo": {"min_freq": -1}}})
        with self.assertRaises(ValueError):
            validate_system_config({"chat": {"typo": {"enable_correction_quote": 1}}})
        with self.assertRaises(ValueError):
            validate_system_config({"chat": {"typo": {"correction_quote_probability": 2}}})


class _Config:
    """提供发送路径所需的最小配置。"""

    def get_system(self) -> dict[str, object]:
        return {
            "chat": {
                "max_context_size": 10,
                "behavior": {"group_quote_frequency": "never", "private_quote_frequency": "never"},
            }
        }


class _Matcher:
    """记录发送载荷，可按需返回回执。"""

    def __init__(self, receipt: object = None) -> None:
        self.sent: list[object] = []
        self.receipt = receipt

    async def send(self, payload: object) -> object:
        self.sent.append(payload)
        return self.receipt


class SendTypoTests(unittest.IsolatedAsyncioTestCase):
    """发送文本改错字，纠正单独补发，历史记实际发出的文本。"""

    def _request(self) -> ChatRequest:
        return ChatRequest(
            session_id="tenant",
            user_id="user",
            group_id="group",
            text="你好",
            turn_id="turn",
            dedupe_key="msg:1",
            trigger_kind="mention",
        )

    async def _send(self, matcher: _Matcher, typo: TypoText, *, quote: bool = False, **patches: object) -> bool:
        store = ChatSessionStore()
        request = self._request()
        response = ChatResponse(text="干净文本。", epoch=0, metadata={"reply_parts": ["干净文本。"]})
        store.append("tenant", {"role": "user", "content": "你好"})
        with ExitStack() as stack:
            stack.enter_context(patch.object(service_module, "get_config_manager", return_value=_Config()))
            stack.enter_context(patch.object(service_module.memory_state, "is_ready", return_value=False))
            stack.enter_context(patch.object(sender_module, "apply_typo", return_value=typo))
            stack.enter_context(patch.object(sender_module, "typing_time", return_value=0.0))
            stack.enter_context(patch.object(sender_module, "_quote_correction", new=lambda: quote))
            for name, value in patches.items():
                stack.enter_context(patch.object(sender_module, name, new=value))
            sent = await ChatService(store)._send_parts(
                request, response, ToolRuntime(bot=object(), matcher=matcher), matcher
            )
        self.history = store.get("tenant")
        return sent

    async def test_send_uses_typo_and_history_records_it(self) -> None:
        matcher = _Matcher()
        sent = await self._send(matcher, TypoText("错字文本。"))

        self.assertTrue(sent)
        self.assertEqual(matcher.sent, ["错字文本。"])
        self.assertEqual(
            self.history,
            [
                {"role": "user", "content": "你好"},
                {"role": "assistant", "content": "错字文本。"},
            ],
        )

    async def test_correction_is_sent_as_extra_message(self) -> None:
        matcher = _Matcher()
        await self._send(matcher, TypoText("错字文本。", "正"))

        self.assertEqual(matcher.sent, ["错字文本。", "正"])
        self.assertEqual(self.history[-1]["content"], "错字文本。")

    async def test_correction_quotes_typo_message(self) -> None:
        matcher = _Matcher(receipt=SimpleNamespace(message_id=42))
        await self._send(
            matcher,
            TypoText("错字文本。", "正"),
            quote=True,
            build_reply_segment=lambda _bot, message_id: f"reply:{message_id}",
            build_message_segment=lambda _bot, _type, text: f"text:{text}",
            build_message=lambda _bot, *segments: segments,
        )

        self.assertEqual(matcher.sent, ["错字文本。", ("reply:42", "text:正")])

    async def test_correction_failure_is_swallowed(self) -> None:
        matcher = _Matcher()
        sends = matcher.send

        async def fail_second(payload: object) -> object:
            if matcher.sent:
                raise RuntimeError("bad")
            return await sends(payload)

        matcher.send = fail_second  # type: ignore[method-assign]
        sent = await self._send(matcher, TypoText("错字文本。", "正"))

        self.assertTrue(sent)
        self.assertEqual(matcher.sent, ["错字文本。"])
