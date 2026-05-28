"""视频解析消息发送。"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from nonebot.adapters import Bot, Event
from nonebot.matcher import Matcher

from ...adapter import build_message, build_message_segment, is_onebot_v11
from .config import cfg_general
from .types import VideoResult


def _summary(result: VideoResult) -> str:
    """构造标题文本。"""
    lines = [f"{result.platform} | {result.display_title}"]
    if result.duration:
        lines.append(f"时长：{int(result.duration)} 秒")
    return "\n".join(lines)


async def send_video_result(matcher: Matcher, bot: Bot, event: Event, result: VideoResult, video_path: Path) -> None:
    """发送标题、封面、视频组成的转发消息。"""
    text_seg = build_message_segment(bot, "text", _summary(result) + "\n")
    image_seg = build_message_segment(bot, "image", result.cover_url) if result.cover_url else None
    video_seg = build_message_segment(bot, "video", _video_payload(video_path))
    message = build_message(bot, text_seg, image_seg, video_seg)

    forward_messages = [build_message(bot, text_seg)]
    if image_seg is not None:
        forward_messages.append(build_message(bot, image_seg))
    forward_messages.append(build_message(bot, video_seg))

    if is_onebot_v11(bot) and await _try_send_onebot_forward(bot, event, forward_messages):
        return

    await matcher.finish(message)


async def send_image_result(matcher: Matcher, bot: Bot, event: Event, result: VideoResult, image_paths: list[Path]) -> None:
    """发送标题和图片组成的转发消息。"""
    text_seg = build_message_segment(bot, "text", _summary(result) + "\n")
    image_segments = [
        build_message_segment(bot, "image", image_path)
        for image_path in image_paths
    ]
    message = build_message(bot, text_seg, *image_segments)

    forward_messages = [build_message(bot, text_seg)]
    forward_messages.extend(build_message(bot, image_seg) for image_seg in image_segments)

    if is_onebot_v11(bot) and await _try_send_onebot_forward(bot, event, forward_messages):
        return

    await matcher.finish(message)


def _video_payload(video_path: Path) -> Path | str:
    """根据配置决定视频发送载荷。"""
    if bool(cfg_general().get("use_base64", False)):
        raw = base64.b64encode(video_path.read_bytes()).decode("ascii")
        return "base64://" + raw
    return video_path


async def _try_send_onebot_forward(bot: Bot, event: Event, messages: list[Any]) -> bool:
    """尝试发送 OneBot V11 合并转发。"""
    try:
        from nonebot.adapters.onebot.v11 import MessageSegment
    except Exception:
        return False

    user_id_raw = str(getattr(bot, "self_id", "0") or "0")
    user_id: int | str = int(user_id_raw) if user_id_raw.isdigit() else user_id_raw
    nickname = "FxBot"
    if not hasattr(MessageSegment, "node_custom"):
        return False
    nodes = [
        MessageSegment.node_custom(user_id=user_id, nickname=nickname, content=message)
        for message in messages
    ]

    try:
        group_id = getattr(event, "group_id", None)
        if group_id is not None:
            if hasattr(bot, "send_group_forward_msg"):
                await bot.send_group_forward_msg(group_id=int(group_id), messages=nodes)
            else:
                await bot.call_api("send_group_forward_msg", group_id=int(group_id), messages=nodes)
            return True

        user = getattr(event, "user_id", None)
        if user is not None:
            if hasattr(bot, "send_private_forward_msg"):
                await bot.send_private_forward_msg(user_id=int(user), messages=nodes)
            else:
                await bot.call_api("send_private_forward_msg", user_id=int(user), messages=nodes)
            return True
    except Exception:
        return False
    return False
