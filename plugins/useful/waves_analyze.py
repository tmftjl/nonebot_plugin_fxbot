"""鸣潮面板评分命令。"""

from __future__ import annotations

import base64
import asyncio
from io import BytesIO
from collections.abc import Iterable

import httpx
from PIL import Image
from nonebot import logger
from nonebot.params import RegexGroup
from nonebot.matcher import Matcher
from nonebot.adapters import Bot, Event

from .config import cfg_waves_analyze
from ...plugin import Plugin
from ...adapter import (
    selfBot,
    build_message,
    event_message,
    fetch_image_bytes,
    build_message_segment,
    extract_reply_message_id,
    image_sources_from_event_or_reply,
)
from ...permission import PermLevel, PermScene
from ...utils.http import get_shared_async_client

P = Plugin(
    "useful",
    display_name="实用工具",
    enabled=True,
    level=PermLevel.LOW,
    scene=PermScene.ALL,
)


waves_analyze_cmd = P.on_regex(
    r"^ww(?:评分|分析)\s*(.*)$",
    name="waves_analyze",
    display_name="鸣潮分析评分",
    priority=5,
    block=True,
    level=PermLevel.MEMBER,
    scene=PermScene.ALL,
)


def _build_command_str(raw_text: str) -> str:
    """整理命令串：``|`` 当分隔符，合并多余空白。

    ``1c``、``1cost`` 都是评分服务认的有效写法，不能删其中的 ``c``/``ost``。
    """
    return " ".join(raw_text.replace("|", " ").split())


_MAX_IMAGE_BYTES = 2 * 1024 * 1024
_MIN_WEBP_QUALITY = 10


def _encode_image_to_b64(image_bytes: bytes) -> str:
    """把单张图片压成 WEBP；降质到底仍超限时按面积比例缩小。"""
    with Image.open(BytesIO(image_bytes)) as image:
        if image.mode != "RGB":
            image = image.convert("RGB")
        buffer = BytesIO()
        quality = 100
        while True:
            buffer.seek(0)
            buffer.truncate()
            image.save(buffer, format="WEBP", quality=quality)
            if buffer.tell() < _MAX_IMAGE_BYTES or quality <= _MIN_WEBP_QUALITY:
                break
            quality -= 5
        while buffer.tell() >= _MAX_IMAGE_BYTES and min(image.size) > 1:
            scale = (_MAX_IMAGE_BYTES / buffer.tell()) ** 0.5
            size = (max(1, int(image.width * scale)), max(1, int(image.height * scale)))
            if size == image.size:
                break
            image = image.resize(size, Image.LANCZOS)
            buffer.seek(0)
            buffer.truncate()
            image.save(buffer, format="WEBP", quality=80)
        if buffer.tell() >= _MAX_IMAGE_BYTES:
            logger.warning(f"[waves_analyze] 图片压缩后仍超过 {_MAX_IMAGE_BYTES} 字节，按当前结果发送")
        return base64.b64encode(buffer.getvalue()).decode("utf-8")


def _encode_images_to_b64(images: Iterable[bytes]) -> list[str]:
    """把图片批量转成 WEBP base64；单张压缩失败时退回原图。"""
    encoded: list[str] = []
    for image_bytes in images:
        try:
            encoded.append(_encode_image_to_b64(image_bytes))
        except Exception:
            logger.opt(exception=True).warning("[waves_analyze] 图片压缩失败，使用原图")
            encoded.append(base64.b64encode(image_bytes).decode("utf-8"))
    return encoded


def _readable_detail(data: object) -> str | None:
    """从响应 JSON 里取出可读说明，取不到时返回 None。"""
    detail: object = data
    if isinstance(data, dict):
        detail = data.get("detail") or data.get("message") or data.get("error") or data.get("msg")
    if isinstance(detail, dict):
        detail = detail.get("message") or detail.get("detail") or detail.get("msg")
    if isinstance(detail, list):
        parts = []
        for item in detail:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("msg") or item.get("message") or item.get("detail")
                if text:
                    parts.append(str(text))
        detail = "；".join(parts)
    if isinstance(detail, str):
        detail = detail.strip()
        return detail[:200] or None
    return None


def _extract_error_detail(response: httpx.Response) -> str | None:
    """从评分服务的错误响应里取出可读说明，取不到时返回 None。

    上游用 4xx 的 ``detail`` 字段说明业务错误，直接把这个文本交给用户即可；
    状态码只留在日志里，不暴露给用户。
    """
    content_type = response.headers.get("content-type", "").lower()
    text = response.text
    if "json" in content_type or text.lstrip().startswith(("{", "[")):
        try:
            detail = _readable_detail(response.json())
        except Exception:
            detail = None
        if detail is not None:
            return detail
    if "text/plain" in content_type:
        return text.strip()[:200] or None
    return None


async def _post_score(images_b64: list[str], command_str: str) -> tuple[bytes | None, str | None]:
    """调用评分服务。"""
    cfg = cfg_waves_analyze()
    api_url = str(cfg["api_url"]).strip()
    token = str(cfg["token"]).strip()
    if not api_url or not token:
        return None, "未配置评分服务地址或 Token"
    payload = {
        "command_str": _build_command_str(command_str),
        "images_base64": images_b64,
    }
    command_display = payload["command_str"]
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        client = await get_shared_async_client()
        response = await client.post(api_url, headers=headers, json=payload, timeout=120.0)
    except Exception:
        logger.opt(exception=True).warning(f"[waves_analyze] 请求评分服务失败: command={command_display!r}")
        return None, "评分服务连接失败，请稍后再试"

    if response.status_code != 200:
        detail = _extract_error_detail(response)
        logger.warning(
            f"[waves_analyze] 评分服务状态异常: {response.status_code} "
            f"command={command_display!r} {response.text[:200]}"
        )
        if response.status_code in (401, 403):
            return None, "评分服务鉴权失败，请联系管理员"
        if response.status_code == 429:
            return None, "评分请求过于频繁，请稍后再试"
        return None, detail or "评分服务暂时不可用，请稍后再试"

    try:
        data = response.json()
    except Exception:
        logger.opt(exception=True).warning(
            f"[waves_analyze] 评分服务返回内容无法解析: command={command_display!r} {response.text[:200]}"
        )
        return None, "评分服务返回了无法解析的内容，请稍后再试"

    message = data.get("message") if isinstance(data, dict) else None
    result_b64 = data.get("result_image_base64") if isinstance(data, dict) else None
    if not result_b64:
        return None, _readable_detail(data) or "评分服务未返回结果图片"
    try:
        image_bytes = base64.b64decode(result_b64)
    except Exception:
        logger.opt(exception=True).warning(f"[waves_analyze] 评分结果图片解析失败: command={command_display!r}")
        return None, "评分结果图片解析失败，请稍后再试"
    logger.info(
        f"[waves_analyze] 评分成功: command={command_display!r} "
        f"images={len(images_b64)} result_bytes={len(image_bytes)}"
    )
    return image_bytes, str(message) if message else None


@waves_analyze_cmd.handle()
async def _handle_waves_analyze(matcher: Matcher, bot: Bot, event: Event, groups: tuple = RegexGroup()) -> None:
    """处理鸣潮评分命令。"""
    command_str = str(groups[0] if groups else "").strip()
    if not command_str:
        await matcher.finish("命令格式错误，参考：ww评分 土豆 1c")

    image_sources = await image_sources_from_event_or_reply(bot, event)
    if not image_sources:
        # 和 get_reply_info 的判断一致，但只看事件本身，不再多调一次平台接口
        has_reply = selfBot.has_reply_reference(event) or extract_reply_message_id(event_message(event)) is not None
        if has_reply:
            await matcher.finish("无法获取该引用图片，请引用最近20条消息内的图片")
        await matcher.finish("未获取到图片，请发送带图片的消息或回复/引用带图消息")

    results = await asyncio.gather(
        *[asyncio.create_task(fetch_image_bytes(source)) for source in image_sources],
        return_exceptions=True,
    )
    errors = [item for item in results if isinstance(item, Exception)]
    if errors:
        logger.opt(exception=errors[0]).warning(f"[waves_analyze] 获取图片失败: command={command_str!r}")
        await matcher.finish("图片获取失败，请重新发送或引用图片")
    image_bytes = [item for item in results if isinstance(item, bytes) and item]
    if not image_bytes:
        await matcher.finish("无法获取该引用图片，请引用时间最近的图片")

    try:
        images_b64 = await asyncio.to_thread(_encode_images_to_b64, image_bytes)
    except Exception:
        logger.opt(exception=True).warning(f"[waves_analyze] 图片处理失败: command={command_str!r}")
        await matcher.finish("图片处理失败，请重新发送图片")
    result_image, tip = await _post_score(images_b64, command_str)
    if not result_image:
        await matcher.finish(f"分析失败: {tip}" if tip else "分析失败，请重试")
    await matcher.finish(build_message(bot, build_message_segment(bot, "image", result_image)))
