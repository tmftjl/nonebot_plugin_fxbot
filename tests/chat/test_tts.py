"""语音合成：OpenAI TTS Provider、路由配置与发送侧补发。"""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.plugins.nonebot_plugin_fxbot.chat import voice
from src.plugins.nonebot_plugin_fxbot.chat.providers import ProviderType
from src.plugins.nonebot_plugin_fxbot.chat.providers.base import TTSProvider
from src.plugins.nonebot_plugin_fxbot.chat.message_receive import sender
from src.plugins.nonebot_plugin_fxbot.chat.providers.register import provider_registry
from src.plugins.nonebot_plugin_fxbot.chat.providers.sources.openai import OpenAITTSProvider


class _Config:
    def __init__(self, chat: dict[str, object]) -> None:
        self._chat = chat

    def get_system(self) -> dict[str, object]:
        return {"chat": self._chat}


class VoiceConfigTests(unittest.TestCase):
    def test_tts_enabled_tolerates_bad_config(self) -> None:
        with patch.object(voice, "get_config_manager", return_value=_Config({"voice": {"enable_tts": True}})):
            self.assertTrue(voice.tts_enabled())
        with patch.object(voice, "get_config_manager", return_value=_Config({"voice": {"enable_tts": False}})):
            self.assertFalse(voice.tts_enabled())
        with patch.object(voice, "get_config_manager", side_effect=RuntimeError):
            self.assertFalse(voice.tts_enabled())
        with patch.object(voice, "get_config_manager", return_value=_Config({"voice": []})):
            self.assertFalse(voice.tts_enabled())

    def test_tts_routing_name(self) -> None:
        chat = {"model_routing": {"tts": " speech "}}
        with patch.object(voice, "get_config_manager", return_value=_Config(chat)):
            self.assertEqual(voice.tts_routing_name(), "speech")
        with patch.object(voice, "get_config_manager", side_effect=RuntimeError):
            self.assertEqual(voice.tts_routing_name(), "")


class SynthesizeTests(unittest.IsolatedAsyncioTestCase):
    async def test_uses_routing_and_timeout(self) -> None:
        provider = SimpleNamespace(config={"timeout": 5}, synthesize=AsyncMock(return_value=b"mp3"))
        with (
            patch.object(voice, "tts_routing_name", return_value="speech"),
            patch.object(voice.provider_manager, "get_tts_provider", AsyncMock(return_value=provider)) as getter,
        ):
            self.assertEqual(await voice.synthesize(" 你好 "), b"mp3")
        getter.assert_awaited_once_with("speech")
        provider.synthesize.assert_awaited_once_with("你好")

    async def test_degrades_to_none(self) -> None:
        self.assertIsNone(await voice.synthesize("  "))
        with patch.object(voice.provider_manager, "get_tts_provider", AsyncMock(side_effect=ValueError("无"))):
            self.assertIsNone(await voice.synthesize("你好"))
        provider = SimpleNamespace(config={}, synthesize=AsyncMock(side_effect=RuntimeError("boom")))
        with patch.object(voice.provider_manager, "get_tts_provider", AsyncMock(return_value=provider)):
            self.assertIsNone(await voice.synthesize("你好"))
        empty = SimpleNamespace(config={}, synthesize=AsyncMock(return_value=b""))
        with patch.object(voice.provider_manager, "get_tts_provider", AsyncMock(return_value=empty)):
            self.assertIsNone(await voice.synthesize("你好"))


class OpenAITTSProviderTests(unittest.IsolatedAsyncioTestCase):
    def test_registered(self) -> None:
        self.assertIs(provider_registry["openai_tts"].cls_type, OpenAITTSProvider)
        self.assertIs(provider_registry["openai_tts"].provider_type, ProviderType.TTS)

    async def test_synthesize_calls_speech_endpoint(self) -> None:
        provider = OpenAITTSProvider(
            "speech",
            {"api_key": "k", "base_url": "https://x/v1", "timeout": 5, "model": "tts-1", "voice": "nova"},
        )
        create = AsyncMock(return_value=SimpleNamespace(content=b"mp3-bytes"))
        provider.client = SimpleNamespace(audio=SimpleNamespace(speech=SimpleNamespace(create=create)))
        self.assertEqual(await provider.synthesize("你好"), b"mp3-bytes")
        self.assertEqual(create.await_args.kwargs["model"], "tts-1")
        self.assertEqual(create.await_args.kwargs["voice"], "nova")
        self.assertEqual(create.await_args.kwargs["input"], "你好")
        self.assertEqual(create.await_args.kwargs["response_format"], "mp3")

    def test_is_tts_provider(self) -> None:
        provider = OpenAITTSProvider(
            "speech", {"api_key": "k", "base_url": "https://x/v1", "timeout": 5, "model": "tts-1"}
        )
        self.assertIsInstance(provider, TTSProvider)
        self.assertEqual(provider.meta().provider_type, ProviderType.TTS)


class SendVoiceTests(unittest.IsolatedAsyncioTestCase):
    def _runtime(self) -> SimpleNamespace:
        return SimpleNamespace(bot=object())

    async def test_disabled_does_not_synthesize(self) -> None:
        matcher = SimpleNamespace(send=AsyncMock())
        with (
            patch.object(sender, "tts_enabled", return_value=False),
            patch.object(sender, "synthesize", AsyncMock(side_effect=AssertionError("不应合成"))),
        ):
            self.assertFalse(await sender.send_voice(self._runtime(), matcher, "你好"))
        matcher.send.assert_not_awaited()

    async def test_sends_record_segment(self) -> None:
        matcher = SimpleNamespace(send=AsyncMock())
        segment = SimpleNamespace(type="record")
        with (
            patch.object(sender, "tts_enabled", return_value=True),
            patch.object(sender, "synthesize", AsyncMock(return_value=b"mp3")),
            patch.object(sender, "build_message_segment", return_value=segment) as build_segment,
            patch.object(sender, "build_message", return_value="payload") as build_message,
        ):
            self.assertTrue(await sender.send_voice(self._runtime(), matcher, "你好"))
        build_segment.assert_called_once()
        self.assertEqual(build_segment.call_args.args[1:], ("record", b"mp3"))
        build_message.assert_called_once()
        matcher.send.assert_awaited_once_with("payload")

    async def test_failures_only_skip(self) -> None:
        matcher = SimpleNamespace(send=AsyncMock())
        with (
            patch.object(sender, "tts_enabled", return_value=True),
            patch.object(sender, "synthesize", AsyncMock(return_value=None)),
        ):
            self.assertFalse(await sender.send_voice(self._runtime(), matcher, "你好"))
        matcher.send.assert_not_awaited()

        with (
            patch.object(sender, "tts_enabled", return_value=True),
            patch.object(sender, "synthesize", AsyncMock(return_value=b"mp3")),
            patch.object(sender, "build_message_segment", return_value="segment"),
            patch.object(sender, "build_message", return_value="payload"),
        ):
            matcher.send = AsyncMock(side_effect=RuntimeError("boom"))
            self.assertFalse(await sender.send_voice(self._runtime(), matcher, "你好"))


if __name__ == "__main__":
    asyncio.run(unittest.main())
