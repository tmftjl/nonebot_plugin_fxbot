"""视频解析消息发送。"""

from __future__ import annotations

import base64
from pathlib import Path

from nonebot.adapters import Bot, Event
from nonebot.matcher import Matcher

from ...adapter import build_message, build_message_segment, send_forward_messages
from .config import cfg_general
from .types import VideoResult


def _summary(result: VideoResult) -> str:
    """构造标题文本。"""
    lines = [f"{result.platform} | {result.display_title}"]
    if result.duration:
        lines.append(f"时长：{int(result.duration)} 秒")
    return "\n".join(lines)


async def send_video_result(matcher: Matcher, bot: Bot, event: Event, result: VideoResult, video_path: Path) -> None:
    """发送标题、封面和视频。"""
    text_seg = build_message_segment(bot, "text", _summary(result) + "\n")
    image_seg = build_message_segment(bot, "image", result.cover_url) if result.cover_url else None
    video_seg = build_message_segment(bot, "video", _video_payload(video_path))

    forward_messages = [build_message(bot, text_seg)]
    if image_seg is not None:
        forward_messages.append(build_message(bot, image_seg))
    forward_messages.append(build_message(bot, video_seg))

    if not await send_forward_messages(bot, event, forward_messages):
        await matcher.finish("当前适配器不支持发送转发消息")


async def send_image_result(matcher: Matcher, bot: Bot, event: Event, result: VideoResult, image_paths: list[Path]) -> None:
    """发送标题和图片。"""
    text_seg = build_message_segment(bot, "text", _summary(result) + "\n")
    image_segments = [
        build_message_segment(bot, "image", image_path)
        for image_path in image_paths
    ]

    forward_messages = [build_message(bot, text_seg)]
    forward_messages.extend(build_message(bot, image_seg) for image_seg in image_segments)

    if not await send_forward_messages(bot, event, forward_messages):
        await matcher.finish("当前适配器不支持发送转发消息")


def _video_payload(video_path: Path) -> Path | str:
    """根据配置决定视频发送载荷。"""
    if bool(cfg_general().get("use_base64", False)):
        raw = base64.b64encode(video_path.read_bytes()).decode("ascii")
        return "base64://" + raw
    return video_path
