"""鸣潮面板评分命令。"""

from __future__ import annotations

import asyncio
import base64
import re
from collections.abc import Iterable
from io import BytesIO
from typing import Any

from nonebot import logger
from nonebot.adapters import Bot, Event
from nonebot.matcher import Matcher
from PIL import Image

from ...permission import PermLevel, PermScene
from ...plugin import Plugin
from ...utils.compat import build_message, build_message_segment
from ...utils.http import get_shared_async_client
from .config import cfg_waves_analyze

P = Plugin("useful", display_name="实用工具", enabled=True, level=PermLevel.LOW, scene=PermScene.ALL)

waves_analyze_cmd = P.on_regex(
    r"^(.*)ww(评分|分析)\s*(.+)",
    name="waves_analyze",
    display_name="鸣潮分析评分",
    priority=5,
    block=True,
    level=PermLevel.MEMBER,
    scene=PermScene.ALL,
)


def _build_command_str(raw_text: str) -> str:
    """清理评分服务不识别的多余字符。"""
    return raw_text.replace("C", "").replace("c", "").replace("ost", "").replace("OST", "").replace("|", " ").strip()


async def _fetch_bytes_from_source(src: str | bytes) -> bytes | None:
    """读取图片来源。"""
    if isinstance(src, bytes):
        return src
    value = str(src or "").strip()
    if not value:
        return None
    if value.startswith("base64://"):
        try:
            return base64.b64decode(value[len("base64://") :])
        except Exception:
            return None
    if value.startswith(("http://", "https://")):
        try:
            client = await get_shared_async_client()
            response = await client.get(value, follow_redirects=True)
            response.raise_for_status()
            return response.content
        except Exception:
            return None
    try:
        from pathlib import Path

        return Path(value).read_bytes()
    except Exception:
        return None


async def _encode_images_to_b64(images: Iterable[bytes]) -> list[str]:
    """将图片转换为 WEBP 并压缩。"""
    encoded: list[str] = []
    max_size = 2 * 1024 * 1024
    for image_bytes in images:
        with Image.open(BytesIO(image_bytes)) as image:
            if image.mode != "RGB":
                image = image.convert("RGB")
            buffer = BytesIO()
            quality = 100
            while quality > 10:
                buffer.seek(0)
                buffer.truncate()
                image.save(buffer, format="WEBP", quality=quality)
                if buffer.tell() < max_size:
                    break
                quality -= 5
            encoded.append(base64.b64encode(buffer.getvalue()).decode("utf-8"))
    return encoded


async def _post_score(images_b64: list[str], command_str: str) -> tuple[bytes | None, str | None]:
    """调用评分服务。"""
    cfg = cfg_waves_analyze()
    api_url = str(cfg.get("api_url", "")).strip()
    token = str(cfg.get("token", "")).strip()
    if not api_url or not token:
        return None, "未配置评分服务地址或 Token"
    payload = {"command_str": _build_command_str(command_str), "images_base64": images_b64}
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        client = await get_shared_async_client()
        response = await client.post(api_url, headers=headers, json=payload, timeout=120.0)
        if response.status_code != 200:
            logger.warning(f"[waves_analyze] 评分服务状态异常: {response.status_code} {response.text[:200]}")
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        return None, f"请求评分服务失败: {exc}"

    message = data.get("message") if isinstance(data, dict) else None
    result_b64 = data.get("result_image_base64") if isinstance(data, dict) else None
    if not result_b64:
        return None, str(message or "评分服务未返回结果图片")
    try:
        return base64.b64decode(result_b64), str(message) if message else None
    except Exception as exc:
        return None, f"结果图片解析失败: {exc}"


def _iter_message_segments(message: Any) -> list[Any]:
    """遍历消息段。"""
    if message is None:
        return []
    try:
        return list(message)
    except Exception:
        return []


def _extract_image_sources(message: Any) -> list[str | bytes]:
    """从消息中提取图片来源。"""
    sources: list[str | bytes] = []
    for segment in _iter_message_segments(message):
        seg_type = getattr(segment, "type", None)
        data = getattr(segment, "data", {}) or {}
        if isinstance(segment, dict):
            seg_type = segment.get("type")
            data = segment.get("data") or {}
        if seg_type != "image":
            continue
        url = data.get("url")
        if isinstance(url, str) and url.strip():
            sources.append(url.strip())
            continue
        file_value = data.get("file")
        if isinstance(file_value, (bytes, str)) and (file_value if isinstance(file_value, bytes) else file_value.strip()):
            sources.append(file_value)
    return sources


async def _get_images_from_event_or_reply(bot: Bot, event: Event) -> list[str | bytes]:  # noqa: ARG001
    """从当前消息或回复消息中提取图片。"""
    try:
        message = event.get_message()
    except Exception:
        message = getattr(event, "message", None)
    sources = _extract_image_sources(message)
    if sources:
        return sources
    reply = getattr(event, "reply", None)
    reply_message = getattr(reply, "message", None) if reply else None
    return _extract_image_sources(reply_message)


@waves_analyze_cmd.handle()
async def _handle_waves_analyze(matcher: Matcher, bot: Bot, event: Event) -> None:
    """处理鸣潮评分命令。"""
    plain_text = event.get_plaintext().strip() if hasattr(event, "get_plaintext") else ""
    match = re.search(r"ww(?:评分|分析)\s*(.+)", plain_text)
    if not match:
        await matcher.finish("命令格式错误，参考：ww评分 土豆 1c")
    command_str = match.group(1).strip()

    image_sources = await _get_images_from_event_or_reply(bot, event)
    if not image_sources:
        await matcher.finish("未获取到图片，请发送带图片的消息或回复/引用带图消息")

    results = await asyncio.gather(*[asyncio.create_task(_fetch_bytes_from_source(source)) for source in image_sources])
    image_bytes = [item for item in results if item]
    if not image_bytes:
        await matcher.finish("未能读取到有效的图片数据")

    images_b64 = await _encode_images_to_b64(image_bytes)
    result_image, tip = await _post_score(images_b64, command_str)
    if not result_image:
        await matcher.finish(f"分析失败: {tip}" if tip else "分析失败，请重试")
    await matcher.finish(build_message(bot, build_message_segment(bot, "image", result_image)))
