"""视觉模型：图片描述与表情标签。"""

from __future__ import annotations

import re
import base64
import asyncio
from io import BytesIO
from typing import Any

import numpy as np
from PIL import Image, ImageSequence
from nonebot import logger

from .memory.prompts import learning_prompt
from .message_adapter import load_images

_MAX_IMAGE_DESC_CHARS = 200


def vision_configured(chat_cfg: dict[str, Any]) -> bool:
    """是否配置了 vision 路由；与 MaiBot 未配视觉模型时不识图一致。"""
    routing = chat_cfg.get("model_routing") or {}
    return isinstance(routing, dict) and bool(str(routing.get("vision") or "").strip())


def clean_description(text: str | None) -> str:
    """把描述压成单行并限长，避免破坏历史里的占位格式。"""
    value = " ".join(str(text or "").split())
    value = value.replace("[", "【").replace("]", "】")
    return value[:_MAX_IMAGE_DESC_CHARS]


async def _chat_vision(provider: Any, url: str, prompt: str, *, max_tokens: int = 256) -> str | None:
    """调用视觉模型，失败时返回 None 以区分正常空响应。"""
    try:
        response = await provider.chat(
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": url}},
                    ],
                }
            ],
            max_tokens=max_tokens,
        )
    except Exception as exc:
        logger.opt(exception=True).debug("[Chat] 图片描述生成失败")
        logger.warning(f"[Chat] 图片描述生成失败，已跳过: {type(exc).__name__}: {exc}")
        return None
    return str(getattr(response, "content", "") or "")


async def describe_image(provider: Any, url: str) -> str:
    """让模型为单张图片生成文字描述，失败时返回空串。"""
    return clean_description(await _chat_vision(provider, url, learning_prompt("image_description"), max_tokens=8192))


async def describe_images(provider: Any, images: list[str]) -> list[str]:
    """并发描述本轮图片，顺序与 images 一致。"""
    if not images:
        return []
    return list(await asyncio.gather(*(describe_image(provider, url) for url in images)))


async def describe_sources(provider: Any, images: list[str | bytes]) -> list[str]:
    """把原始图片源加载后并发描述。"""

    async def describe_source(source: str | bytes) -> str:
        loaded = await load_images([source])
        return await describe_image(provider, loaded[0]) if loaded else ""

    tasks = [asyncio.create_task(describe_source(source)) for source in images]
    try:
        return list(await asyncio.gather(*tasks))
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def _parse_emoji(text: str) -> tuple[str, list[str]]:
    """读取上游纯文本情绪标签，保留首次出现的大小写并去重。"""
    tags = []
    seen = set()
    for part in re.split(r"[,，、;；\r\n\t]+", str(text or "").strip()):
        tag = part.strip()
        if tag and tag.lower() not in seen:
            seen.add(tag.lower())
            tags.append(tag)
    return ",".join(tags), tags


def emoji_gif_contact_sheet(data: bytes) -> bytes:
    """适配 MaiBot ImageUtils.gif_2_static_image 的选帧和横向 JPEG 拼图。"""
    selected = []
    previous = None
    with Image.open(BytesIO(data)) as image:
        if image.format != "GIF":
            raise ValueError("表情数据不是 GIF")
        for frame in ImageSequence.Iterator(image):
            rgb = frame.convert("RGB")
            current = np.array(rgb)
            if previous is None or np.mean((current - previous) ** 2) > 1000.0:
                selected.append(rgb.copy())
                previous = current
                if len(selected) >= 15:
                    break
    width, height = selected[0].size
    target_width = max(1, int(200 / height * width))
    canvas = Image.new("RGB", (target_width * len(selected), 200))
    for index, frame in enumerate(selected):
        canvas.paste(frame.resize((target_width, 200), Image.Resampling.LANCZOS), (index * target_width, 0))
    output = BytesIO()
    canvas.save(output, format="JPEG", quality=85)
    return output.getvalue()


async def describe_emoji(provider: Any, url: str) -> tuple[str, list[str]]:
    """为表情包生成描述与标签；失败时返回空结果。"""
    animated = url.startswith("data:image/gif;")
    if animated:
        data = await asyncio.to_thread(emoji_gif_contact_sheet, base64.b64decode(url.split(",", 1)[1]))
        url = "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")
    prompt = learning_prompt("emoji_content_analysis", image_type="GIF" if animated else "STATIC")
    raw = await _chat_vision(provider, url, prompt, max_tokens=8192)
    return _parse_emoji(raw) if raw else ("", [])


async def review_emoji(provider: Any, url: str) -> bool:
    """内容审核；模型失败或明确否定时返回 False。"""
    raw = await _chat_vision(provider, url, learning_prompt("emoji_content_filtration"), max_tokens=8192)
    return raw is not None and "否" not in raw
