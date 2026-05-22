"""NapCat 图片外显功能。"""

from __future__ import annotations

import random
from typing import Any

from nonebot.adapters import Event
from nonebot import get_driver, logger
from nonebot.matcher import Matcher

from ...permission import PermLevel, PermScene
from ...plugin import Plugin
from ...adapter import get_onebot_v11_message_segment_class
from ...utils.http import get_shared_async_client
from .config import cfg_image_display, get_config, save_config

P = Plugin("napcat", display_name="NapCat", enabled=True, level=PermLevel.LOW, scene=PermScene.ALL)

_hitokoto_cache = "Ciallo~"
_original_image: Any = None
_patched = False


async def _fetch_hitokoto(api_url: str) -> str:
    """从一言接口刷新外显文本缓存。"""
    global _hitokoto_cache
    try:
        client = await get_shared_async_client()
        response = await client.get(api_url)
        response.raise_for_status()
        text = response.text.strip()
    except Exception:
        logger.opt(exception=True).debug("[NapCat] 获取图片外显一言失败")
        return _hitokoto_cache
    if text:
        _hitokoto_cache = text
    return _hitokoto_cache


def _get_summary_text(cfg: dict[str, Any]) -> str:
    """生成图片外显文本。"""
    display_type = int(cfg["type"])
    if display_type == 1:
        return str(cfg["text"])
    if display_type == 2:
        return _hitokoto_cache
    if display_type == 3:
        items = cfg["list"]
        if isinstance(items, list) and items:
            return str(random.choice(items))
        return "[图片]"
    return "Ciallo~"


def enable_image_summary() -> bool:
    """启用 OneBot V11 图片外显补丁。"""
    global _original_image, _patched

    message_segment = get_onebot_v11_message_segment_class()
    if message_segment is None:
        logger.debug("[NapCat] OneBot V11 未安装，跳过图片外显补丁")
        return False
    if _patched:
        return True

    _original_image = message_segment.image

    def _patched_image(file: Any, *args: Any, **kwargs: Any):
        segment = _original_image(file, *args, **kwargs)
        if cfg_image_display()["enabled"]:
            segment.data["summary"] = _get_summary_text(cfg_image_display())
        return segment

    message_segment.image = _patched_image
    _patched = True
    logger.info("[NapCat] 图片外显功能已启用")
    return True


def disable_image_summary() -> bool:
    """关闭 OneBot V11 图片外显补丁。"""
    global _patched

    message_segment = get_onebot_v11_message_segment_class()
    if message_segment is None or _original_image is None:
        return False
    if _patched:
        message_segment.image = _original_image
        _patched = False
        logger.info("[NapCat] 图片外显功能已关闭")
    return True


toggle_cmd = P.on_regex(
    r"^#(开启|关闭)图片外显",
    name="toggle_image_display",
    display_name="图片外显开关",
    priority=5,
    block=True,
    level=PermLevel.ADMIN,
    scene=PermScene.ALL,
)


@toggle_cmd.handle()
async def _handle_toggle(matcher: Matcher, event: Event) -> None:
    """处理图片外显开关。"""
    text = str(event.get_plaintext()) if hasattr(event, "get_plaintext") else ""
    enabled = "开启" in text
    cfg = get_config()
    cfg["image_display"]["enabled"] = enabled
    save_config(cfg)

    ok = enable_image_summary() if enabled else disable_image_summary()
    status = "开启" if enabled else "关闭"
    if not ok and enabled:
        await matcher.finish("OneBot V11 未安装，无法开启图片外显")
    await matcher.finish(f"已{status}图片外显")


@get_driver().on_startup
async def _init_image_display() -> None:
    """启动时初始化图片外显状态。"""
    cfg = cfg_image_display()
    if cfg["enabled"] and int(cfg["type"]) == 2:
        await _fetch_hitokoto(str(cfg["api"]))
    if cfg["enabled"]:
        enable_image_summary()


if cfg_image_display()["enabled"]:
    enable_image_summary()
