"""语音消息：配置开关、音频提取与 STT Provider 转写。"""

from __future__ import annotations

import base64
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.plugins.nonebot_plugin_fxbot.chat import voice
from src.plugins.nonebot_plugin_fxbot.chat.providers import ProviderType
from src.plugins.nonebot_plugin_fxbot.chat.providers.base import STTProvider
from src.plugins.nonebot_plugin_fxbot.config.system_defaults import (
    SYSTEM_DEFAULTS,
    validate_system_config,
)
from src.plugins.nonebot_plugin_fxbot.chat.providers.register import provider_registry
from src.plugins.nonebot_plugin_fxbot.chat.providers.sources.openai import OpenAISTTProvider


class _Config:
    def __init__(self, chat: dict[str, object]) -> None:
        self._chat = chat

    def get_system(self) -> dict[str, object]:
        return {"chat": self._chat}


def _segments(*items: tuple[str, dict[str, object]]) -> SimpleNamespace:
    return SimpleNamespace(get_message=lambda: [SimpleNamespace(type=kind, data=data) for kind, data in items])


class VoiceConfigTests(unittest.TestCase):
    """接收侧识别与发送侧合成各一个开关。"""

    def test_defaults(self) -> None:
        self.assertEqual(SYSTEM_DEFAULTS["chat"]["voice"], {"enable_asr": False, "enable_tts": False})
        self.assertEqual(SYSTEM_DEFAULTS["chat"]["model_routing"]["voice"], "")
        self.assertEqual(SYSTEM_DEFAULTS["chat"]["model_routing"]["tts"], "")

    def test_validation(self) -> None:
        validate_system_config({"chat": {"voice": {"enable_asr": True}}})
        with self.assertRaisesRegex(ValueError, "chat.voice.enable_asr"):
            validate_system_config({"chat": {"voice": {"enable_asr": "yes"}}})
        with self.assertRaisesRegex(ValueError, "chat.voice.enable_tts"):
            validate_system_config({"chat": {"voice": {"enable_tts": "yes"}}})
        with self.assertRaisesRegex(ValueError, "chat.voice 必须是对象"):
            validate_system_config({"chat": {"voice": []}})

    def test_asr_enabled_tolerates_bad_config(self) -> None:
        with patch.object(voice, "get_config_manager", return_value=_Config({"voice": {"enable_asr": True}})):
            self.assertTrue(voice.asr_enabled())
        with patch.object(voice, "get_config_manager", return_value=_Config({"voice": {"enable_asr": False}})):
            self.assertFalse(voice.asr_enabled())
        with patch.object(voice, "get_config_manager", side_effect=RuntimeError):
            self.assertFalse(voice.asr_enabled())
        with patch.object(voice, "get_config_manager", return_value=_Config({"voice": []})):
            self.assertFalse(voice.asr_enabled())

    def test_voice_routing_name(self) -> None:
        chat = {"model_routing": {"voice": " whisper "}}
        with patch.object(voice, "get_config_manager", return_value=_Config(chat)):
            self.assertEqual(voice.voice_routing_name(), "whisper")
        with patch.object(voice, "get_config_manager", side_effect=RuntimeError):
            self.assertEqual(voice.voice_routing_name(), "")


class AudioSourceTests(unittest.TestCase):
    """语音段字段在各平台不统一，按 url/file/file_id/path 取。"""

    def test_extracts_supported_segments(self) -> None:
        event = _segments(
            ("text", {"text": "你好"}),
            ("record", {"url": "https://a.com/1.mp3"}),
            ("voice", {"file": "abc"}),
            ("audio", {"file_id": "f1"}),
            ("record", {"path": "/tmp/x.amr"}),
            ("record", {}),
        )
        self.assertEqual(
            voice.audio_sources_for_event(event),
            ["https://a.com/1.mp3", "abc", "f1", "/tmp/x.amr"],
        )

    def test_no_message_is_empty(self) -> None:
        self.assertEqual(voice.audio_sources_for_event(SimpleNamespace()), [])

    def test_filename_from_magic(self) -> None:
        cases = (
            (b"RIFF\x00\x00\x00\x00WAVEfmt ", "voice.wav"),
            (b"OggS\x00", "voice.ogg"),
            (b"fLaC\x00", "voice.flac"),
            (b"ID3\x04", "voice.mp3"),
            (b"\xff\xfb\x90", "voice.mp3"),
            (b"\x00\x00\x00\x18ftypM4A ", "voice.m4a"),
            (b"\x1aE\xdf\xa3", "voice.webm"),
            (b"#!AMR\n", "voice.amr"),
            (b"#!SILK_V3", "voice.silk"),
            (b"zzz", "voice.mp3"),
        )
        for data, name in cases:
            with self.subTest(name=name):
                self.assertEqual(voice._audio_filename(data), name)


class LoadAudioTests(unittest.IsolatedAsyncioTestCase):
    """只接受内联字节、base64 和公网 URL。"""

    async def test_bytes_and_base64(self) -> None:
        self.assertEqual(await voice._load_audio(b"abc"), b"abc")
        self.assertEqual(await voice._load_audio("base64://" + base64.b64encode(b"abc").decode()), b"abc")
        self.assertEqual(await voice._load_audio("data:audio/mp3;base64," + base64.b64encode(b"abc").decode()), b"abc")

    async def test_rejects_local_and_oversized(self) -> None:
        with self.assertRaisesRegex(ValueError, "HTTP"):
            await voice._load_audio("C:/secret.mp3")
        with patch.object(voice, "MAX_AUDIO_BYTES", 2):
            with self.assertRaisesRegex(ValueError, "上限"):
                await voice._load_audio(b"abc")

    async def test_downloads_http(self) -> None:
        with patch.object(voice, "_download_audio", AsyncMock(return_value=b"audio")):
            self.assertEqual(await voice._load_audio("https://a.com/x.mp3"), b"audio")


class TranscribeTests(unittest.IsolatedAsyncioTestCase):
    """未启用、无 Provider、转写失败都降级为可读占位文本。"""

    def _event(self) -> SimpleNamespace:
        return _segments(("record", {"url": "https://a.com/1.mp3"}))

    async def test_disabled_without_provider(self) -> None:
        with (
            patch.object(voice, "asr_enabled", return_value=False),
            patch.object(voice, "_load_audio", AsyncMock(side_effect=AssertionError("不应下载"))),
        ):
            self.assertEqual(await voice.transcribe_event(self._event()), voice.VOICE_DISABLED)

    async def test_no_audio_returns_empty(self) -> None:
        self.assertEqual(await voice.transcribe_event(_segments(("text", {"text": "hi"}))), "")

    async def test_missing_provider_reports_failure(self) -> None:
        with (
            patch.object(voice, "asr_enabled", return_value=True),
            patch.object(voice, "_load_audio", AsyncMock(return_value=b"audio")),
            patch.object(voice.provider_manager, "get_stt_provider", AsyncMock(side_effect=ValueError("无"))),
        ):
            self.assertEqual(await voice.transcribe_event(self._event()), voice.VOICE_FAILED)

    async def test_success_and_empty_result(self) -> None:
        provider = SimpleNamespace(config={"timeout": 5}, transcribe=AsyncMock(return_value=" 你好 "))
        with (
            patch.object(voice, "asr_enabled", return_value=True),
            patch.object(voice, "_load_audio", AsyncMock(return_value=b"audio")),
            patch.object(voice.provider_manager, "get_stt_provider", AsyncMock(return_value=provider)),
            patch.object(voice, "voice_routing_name", return_value="whisper"),
        ):
            self.assertEqual(await voice.transcribe_event(self._event()), "[语音: 你好]")
        provider.transcribe = AsyncMock(return_value="")
        with (
            patch.object(voice, "asr_enabled", return_value=True),
            patch.object(voice, "_load_audio", AsyncMock(return_value=b"audio")),
            patch.object(voice.provider_manager, "get_stt_provider", AsyncMock(return_value=provider)),
        ):
            self.assertEqual(await voice.transcribe_event(self._event()), voice.VOICE_FAILED)

    async def test_provider_errors_become_failure(self) -> None:
        provider = SimpleNamespace(config={}, transcribe=AsyncMock(side_effect=RuntimeError("boom")))
        with (
            patch.object(voice, "asr_enabled", return_value=True),
            patch.object(voice, "_load_audio", AsyncMock(return_value=b"audio")),
            patch.object(voice.provider_manager, "get_stt_provider", AsyncMock(return_value=provider)),
        ):
            self.assertEqual(await voice.transcribe_event(self._event()), voice.VOICE_FAILED)

    async def test_transcribe_uses_routing_and_timeout(self) -> None:
        provider = SimpleNamespace(config={"timeout": 5}, transcribe=AsyncMock(return_value="文字"))
        with (
            patch.object(voice, "voice_routing_name", return_value="whisper"),
            patch.object(voice.provider_manager, "get_stt_provider", AsyncMock(return_value=provider)) as getter,
        ):
            self.assertEqual(await voice.transcribe(b"RIFFxxxxWAVEfmt "), "文字")
        getter.assert_awaited_once_with("whisper")
        self.assertEqual(provider.transcribe.await_args.args[1], "voice.wav")


class STTProviderTests(unittest.IsolatedAsyncioTestCase):
    """OpenAI 兼容转写接口注册为 stt 能力。"""

    def test_registered(self) -> None:
        self.assertIs(provider_registry["openai_stt"].cls_type, OpenAISTTProvider)
        self.assertIs(provider_registry["openai_stt"].provider_type, ProviderType.STT)

    async def test_transcribe_calls_audio_endpoint(self) -> None:
        provider = OpenAISTTProvider(
            "w", {"api_key": "k", "base_url": "https://x/v1", "timeout": 5, "model": "whisper-1"}
        )
        create = AsyncMock(return_value=SimpleNamespace(text=" 你好 "))
        provider.client = SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(create=create)))
        self.assertEqual(await provider.transcribe(b"abc", "voice.mp3"), "你好")
        self.assertEqual(create.await_args.kwargs["model"], "whisper-1")
        self.assertEqual(create.await_args.kwargs["file"], ("voice.mp3", b"abc"))

    def test_is_stt_provider(self) -> None:
        provider = OpenAISTTProvider("w", {"api_key": "k", "base_url": "https://x/v1", "timeout": 5, "model": "m"})
        self.assertIsInstance(provider, STTProvider)
        self.assertEqual(provider.meta().provider_type, ProviderType.STT)


if __name__ == "__main__":
    unittest.main()
