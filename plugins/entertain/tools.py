"""娱乐插件 AI 工具。"""

from __future__ import annotations

from typing import Literal

from nonebot import logger

from ...adapter import build_message_segment, selfBot
from ...chat.tools import ToolContext, ToolError, ToolRuntime, tool
from .fortune import (
    _generate_fortune_canvas,
    _get_background_image,
    _get_or_create_today_fortune,
)
from .musicshare import (
    MusicLoginRequired,
    Platform,
    _get_song_url_with_pool,
    _login_hint,
    _search_songs_with_pool,
)


@tool(
    name="get_fortune",
    description="获取今日运势，同一天内相同用户返回相同结果。",
    parameters={
        "type": "object",
        "properties": {
            "nickname": {
                "type": "string",
                "description": "用户昵称，用于显示在运势卡片上。",
                "default": "您",
            }
        },
        "required": [],
    },
)
async def get_fortune_tool(ctx: ToolContext, rt: ToolRuntime, nickname: str = "您") -> str:
    """AI 工具：发送今日运势图片。"""
    try:
        if not ctx.user_id:
            raise ToolError("缺少用户ID", code="missing_user_id")
        data, _ = _get_or_create_today_fortune(ctx.user_id)
        background = await _get_background_image()
        image = _generate_fortune_canvas(nickname, data, background)
        bot = rt.require_bot()
        segment = build_message_segment(bot, "image", image)
        if ctx.group_id:
            await selfBot.send_group_message(ctx.group_id, segment)
        else:
            await selfBot.send_private_message(ctx.user_id, segment)

        fortune = data.get("fortune", {})
        parts = [
            f"运势：{fortune.get('fortuneSummary', '今日运势')}",
            f"星级：{fortune.get('luckyStar', '')}",
            f"签文：{fortune.get('signText', '')}",
            f"详解：{fortune.get('unsignText', '')}",
        ]
        return "\n".join(part for part in parts if part.split("：", 1)[-1])
    except ToolError:
        raise
    except Exception as exc:
        logger.opt(exception=True).warning("[entertain.tools] 获取运势失败")
        raise ToolError(f"获取运势失败：{exc}", code="internal_error") from exc


@tool(
    name="play_music",
    description="搜索并播放歌曲，自动播放搜索结果第一首。",
    parameters={
        "type": "object",
        "properties": {
            "song_name": {"type": "string", "description": "歌曲名称或关键词"},
            "platform": {
                "type": "string",
                "enum": ["qq", "netease"],
                "description": "音乐平台，可选 qq 或 netease，默认 qq。",
                "default": "qq",
            },
        },
        "required": ["song_name"],
    },
)
async def play_music_tool(
    ctx: ToolContext,
    rt: ToolRuntime,
    song_name: str,
    platform: Literal["qq", "netease"] = "qq",
) -> str:
    """AI 工具：搜索并播放歌曲。"""
    try:
        songs = await _search_songs_with_pool(ctx.user_id, platform, song_name)
        if not songs:
            raise ToolError(f"未找到歌曲：{song_name}", code="song_not_found")
        song = songs[0]
        audio_url = await _get_song_url_with_pool(ctx.user_id, platform, song)
        if not audio_url:
            raise ToolError(f"无法获取歌曲播放链接：{song.song}", code="url_unavailable")

        bot = rt.require_bot()
        segment = build_message_segment(bot, "record", audio_url)
        if ctx.group_id:
            await selfBot.send_group_message(ctx.group_id, segment)
        else:
            await selfBot.send_private_message(ctx.user_id, segment)
        suffix = f" - {song.singer}" if song.singer else ""
        return f"正在播放：{song.song}{suffix}"
    except ToolError:
        raise
    except MusicLoginRequired as exc:
        raise ToolError(_login_hint(platform), code="login_required") from exc
    except Exception as exc:
        logger.opt(exception=True).warning("[entertain.tools] 点歌失败")
        raise ToolError(f"点歌失败：{exc}", code="internal_error") from exc
