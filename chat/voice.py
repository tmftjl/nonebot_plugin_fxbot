"""语音消息转写：调用 STT Provider，把结果作为正文的一部分交给后续流程。"""

from __future__ import annotations

import base64
import asyncio
from typing import Any

from nonebot import logger

from ..config import get_manager as get_config_manager
from ..adapter import event_message, extract_raw_audio_sources
from .providers import provider_manager
from ..utils.http import read_limited, ensure_public_http_url, get_shared_async_client
from .providers.usage import track_model_task_usage

MAX_AUDIO_BYTES = 20 * 1024 * 1024
MAX_REDIRECTS = 3
VOICE_DISABLED = "[语音消息]"
VOICE_FAILED = "[语音消息，转录失败]"


def asr_enabled() -> bool:
    """读取语音识别开关；配置异常时按关闭处理。"""
    try:
        voice = get_config_manager().get_system()["chat"].get("voice") or {}
    except Exception:
        return False
    return bool(isinstance(voice, dict) and voice.get("enable_asr"))


def tts_enabled() -> bool:
    """读取语音合成开关；配置异常时按关闭处理。"""
    try:
        settings = get_config_manager().get_system()["chat"].get("voice") or {}
    except Exception:
        return False
    return bool(isinstance(settings, dict) and settings.get("enable_tts"))


def _routing_name(key: str) -> str:
    """读取任务路由里的 Provider 名称。"""
    try:
        routing = get_config_manager().get_system()["chat"].get("model_routing") or {}
    except Exception:
        return ""
    return str(routing.get(key) or "").strip() if isinstance(routing, dict) else ""


def voice_routing_name() -> str:
    """返回任务路由里的语音识别 Provider 名称。"""
    return _routing_name("voice")


def tts_routing_name() -> str:
    """返回任务路由里的语音合成 Provider 名称。"""
    return _routing_name("tts")


def audio_sources_for_event(event: Any) -> list[str | bytes]:
    """提取消息里的语音源；不加锁也不下载。"""
    try:
        return list(dict.fromkeys(source for source in extract_raw_audio_sources(event_message(event)) if source))
    except Exception:
        return []


def _audio_filename(data: bytes) -> str:
    """按文件头猜扩展名；OpenAI 转写接口依靠文件名判断格式。"""
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "voice.wav"
    if data[:4] == b"OggS":
        return "voice.ogg"
    if data[:4] == b"fLaC":
        return "voice.flac"
    if data[:3] == b"ID3" or data[:2] in {b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"}:
        return "voice.mp3"
    if data[4:8] == b"ftyp":
        return "voice.m4a"
    if data[:4] == b"\x1aE\xdf\xa3":
        return "voice.webm"
    if data.startswith(b"#!AMR"):
        return "voice.amr"
    if data.startswith(b"#!SILK"):
        return "voice.silk"
    return "voice.mp3"


async def _download_audio(url: str) -> bytes:
    """下载公网语音：逐跳校验重定向并限制大小。"""
    client = await get_shared_async_client()
    for _ in range(MAX_REDIRECTS + 1):
        await ensure_public_http_url(url)
        async with client.stream("GET", url, follow_redirects=False) as response:
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    raise ValueError("重定向缺少 Location")
                url = str(response.url.join(location))
                continue
            response.raise_for_status()
            return await read_limited(response.aiter_bytes(), response.headers.get("content-length"), MAX_AUDIO_BYTES)
    raise ValueError("重定向次数过多")


async def _load_audio(source: str | bytes) -> bytes:
    """加载语音源；只接受内联字节、base64 和公网 http(s) URL，拒绝本地路径。"""
    if isinstance(source, bytes):
        data = source
    else:
        value = str(source or "").strip()
        if value.startswith("base64://"):
            data = base64.b64decode(value[9:])
        elif value.startswith("data:") and ";base64," in value:
            data = base64.b64decode(value.split(";base64,", 1)[1])
        elif value.startswith(("http://", "https://")):
            data = await _download_audio(value)
        else:
            raise ValueError("语音源不是可下载的 HTTP(S) 地址")
    if len(data) > MAX_AUDIO_BYTES:
        raise ValueError(f"语音超过 {MAX_AUDIO_BYTES // 1024 // 1024} MiB 上限")
    return data


async def transcribe(data: bytes) -> str | None:
    """调用语音识别 Provider；未配置或失败时返回 None。"""
    name = voice_routing_name()
    try:
        provider = await provider_manager.get_stt_provider(name)
    except Exception as exc:
        logger.warning(f"[Chat] 语音识别 Provider 不可用: {exc}")
        return None
    timeout = float(provider.config.get("timeout") or 60)
    try:
        async with track_model_task_usage("voice", provider):
            text = await asyncio.wait_for(provider.transcribe(data, _audio_filename(data)), timeout=timeout)
    except Exception:
        logger.opt(exception=True).warning("[Chat] 语音转写失败")
        return None
    return str(text or "").strip()


async def synthesize(text: str) -> bytes | None:
    """调用语音合成 Provider；未配置、结果为空或失败时返回 None。"""
    value = str(text or "").strip()
    if not value:
        return None
    try:
        provider = await provider_manager.get_tts_provider(tts_routing_name())
    except Exception as exc:
        logger.warning(f"[Chat] 语音合成 Provider 不可用: {exc}")
        return None
    timeout = float(provider.config.get("timeout") or 60)
    try:
        async with track_model_task_usage("tts", provider):
            audio = await asyncio.wait_for(provider.synthesize(value), timeout=timeout)
    except Exception:
        logger.opt(exception=True).warning("[Chat] 语音合成失败")
        return None
    return audio or None


async def transcribe_event(event: Any) -> str:
    """把消息里的语音转成 [语音: 文本]；没有语音返回空串，多条语音取第一条。"""
    sources = audio_sources_for_event(event)
    if not sources:
        return ""
    if not asr_enabled():
        return VOICE_DISABLED
    try:
        data = await _load_audio(sources[0])
        text = await transcribe(data)
    except Exception:
        logger.opt(exception=True).warning("[Chat] 语音消息处理失败")
        text = None
    return f"[语音: {text}]" if text else VOICE_FAILED
