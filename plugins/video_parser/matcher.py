"""视频解析 matcher。"""

from __future__ import annotations

import json
from typing import Any

import httpx
from nonebot.adapters import Bot, Event
from nonebot.exception import MatcherException
from nonebot.matcher import Matcher
from nonebot.params import RegexGroup
from nonebot.permission import SUPERUSER
from nonebot.rule import Rule
from nonebot.typing import T_State

from ...adapter import build_message, build_message_segment
from ...adapter.events import event_group_id
from ...permission import PermLevel, PermScene
from . import P
from .config import is_global_enabled, set_global_enabled
from .downloader import (
    DownloadError,
    cleanup_download_dir,
    cleanup_legacy_cache,
    create_download_dir,
    download_images,
    download_video,
)
from .parsers import ParseError, can_parse_url, find_url, parse_url
from .sender import send_image_result, send_video_result
from .state import is_group_enabled, set_group_enabled

STATE_URL_KEY = "video_parser_url"


def _find_event_url(event: Event) -> str | None:
    """从普通文本和卡片消息段中寻找可解析链接。"""
    if url := _find_card_url(event):
        return url
    return find_url(event.get_plaintext())


def _find_card_url(event: Event) -> str | None:
    """按原插件逻辑从 JSON 卡片 meta 字段提取跳转链接。"""
    try:
        message = event.get_message()
    except Exception:
        return None
    for segment in message:
        data = getattr(segment, "data", {}) or {}
        raw = data.get("raw") or data.get("data")
        if not isinstance(raw, str):
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        meta = payload.get("meta")
        if not isinstance(meta, dict):
            continue
        for key, field in (
            ("detail_1", "qqdocurl"),
            ("news", "jumpUrl"),
            ("music", "jumpUrl"),
        ):
            item = meta.get(key)
            if isinstance(item, dict) and isinstance(item.get(field), str):
                return item[field]
    return None


async def _has_video_url(event: Event, state: T_State) -> bool:
    """判断消息是否包含可解析链接。"""
    if not is_global_enabled():
        return False
    if not is_group_enabled(event_group_id(event)):
        return False
    url = _find_event_url(event)
    if not url:
        return False
    if not can_parse_url(url):
        return False
    state[STATE_URL_KEY] = url
    return True


video_matcher = P.on_message(
    rule=Rule(_has_video_url),
    name="parse",
    display_name="自动解析视频",
    priority=8,
    block=True,
    level=PermLevel.LOW,
    scene=PermScene.ALL,
)

group_toggle_cmd = P.on_regex(
    r"^[#＃]?(开启|关闭)解析$",
    name="group_toggle",
    display_name="群解析开关",
    priority=5,
    block=True,
    level=PermLevel.ADMIN,
    scene=PermScene.GROUP,
)

global_toggle_cmd = P.on_regex(
    r"^[#＃]?全局(开启|关闭)解析$",
    name="global_toggle",
    display_name="全局解析开关",
    priority=5,
    block=True,
    level=PermLevel.SUPERUSER,
    scene=PermScene.ALL,
    permission=SUPERUSER,
)

bili_login_cmd = P.on_regex(
    r"^[#＃]?(?:B站|b站|哔哩哔哩)登录$",
    name="bilibili_login",
    display_name="B站扫码登录",
    priority=5,
    block=True,
    level=PermLevel.SUPERUSER,
    scene=PermScene.ALL,
    permission=SUPERUSER,
)


@video_matcher.handle()
async def _handle_video(
    matcher: Matcher, bot: Bot, event: Event, state: T_State
) -> None:
    """处理视频解析。"""
    url = str(state.get(STATE_URL_KEY) or "")
    if not can_parse_url(url):
        return
    await matcher.send("正在解析媒体，请稍候...")
    cleanup_legacy_cache()
    download_dir = create_download_dir()
    try:
        result = await parse_url(url)
        if result.video_url:
            video_path = await download_video(result, directory=download_dir)
            await send_video_result(matcher, bot, event, result, video_path)
        elif result.image_urls:
            image_paths = await download_images(result, directory=download_dir)
            await send_image_result(matcher, bot, event, result, image_paths)
        else:
            raise ParseError("解析结果没有可发送的媒体")
    except (ParseError, DownloadError) as exc:
        await matcher.finish(f"解析失败：{exc}")
    except httpx.HTTPStatusError as exc:
        await matcher.finish(f"解析失败：平台接口返回 {exc.response.status_code}")
    except MatcherException:
        raise
    except Exception as exc:
        await matcher.finish(f"解析失败：{type(exc).__name__}: {exc}")
    finally:
        cleanup_download_dir(download_dir)


@group_toggle_cmd.handle()
async def _handle_group_toggle(
    matcher: Matcher, event: Event, groups: tuple = RegexGroup()
) -> None:
    """处理本群解析开关。"""
    group_id = event_group_id(event)
    if not group_id:
        await matcher.finish("请在群聊中使用")
    enabled = str(groups[0]) == "开启"
    set_group_enabled(group_id, enabled)
    await matcher.finish(f"本群视频解析已{'开启' if enabled else '关闭'}")


@global_toggle_cmd.handle()
async def _handle_global_toggle(matcher: Matcher, groups: tuple = RegexGroup()) -> None:
    """处理全局解析开关。"""
    enabled = str(groups[0]) == "开启"
    set_global_enabled(enabled)
    await matcher.finish(f"全局视频解析已{'开启' if enabled else '关闭'}")


@bili_login_cmd.handle()
async def _handle_bili_login(matcher: Matcher, bot: Bot) -> None:
    """处理 B 站扫码登录。"""
    from .parsers.bilibili import create_qrcode, poll_qrcode

    try:
        image = await create_qrcode()
    except ParseError as exc:
        await matcher.finish(str(exc))
    await matcher.send(
        build_message(
            bot,
            build_message_segment(bot, "text", "请使用哔哩哔哩客户端扫码登录\n"),
            build_message_segment(bot, "image", image),
        )
    )
    message = await poll_qrcode(matcher.send)
    await matcher.finish(message)
