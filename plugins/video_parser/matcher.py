"""视频解析 matcher。"""

from __future__ import annotations

from nonebot.adapters import Bot, Event
from nonebot.matcher import Matcher
from nonebot.permission import SUPERUSER
from nonebot.rule import Rule
from nonebot.typing import T_State
from nonebot.params import RegexGroup

from ...adapter import build_message, build_message_segment
from ...adapter.support import event_group_id
from ...permission import PermLevel, PermScene
from . import P
from .config import is_global_enabled, set_global_enabled
from .downloader import DownloadError, download_video
from .parsers import ParseError, find_url, parse_url
from .sender import send_video_result
from .state import is_group_enabled, set_group_enabled

STATE_URL_KEY = "video_parser_url"


async def _has_video_url(event: Event, state: T_State) -> bool:
    """判断消息是否包含可解析链接。"""
    if not is_global_enabled():
        return False
    if not is_group_enabled(event_group_id(event)):
        return False
    url = find_url(event.get_plaintext())
    if not url:
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
    r"^#?(开启|关闭)解析$",
    name="group_toggle",
    display_name="群解析开关",
    priority=5,
    block=True,
    level=PermLevel.ADMIN,
    scene=PermScene.GROUP,
)

global_toggle_cmd = P.on_regex(
    r"^#?全局(开启|关闭)解析$",
    name="global_toggle",
    display_name="全局解析开关",
    priority=5,
    block=True,
    level=PermLevel.SUPERUSER,
    scene=PermScene.ALL,
    permission=SUPERUSER,
)

bili_login_cmd = P.on_regex(
    r"^#?(?:B站|b站|哔哩哔哩)登录$",
    name="bilibili_login",
    display_name="B站扫码登录",
    priority=5,
    block=True,
    level=PermLevel.SUPERUSER,
    scene=PermScene.ALL,
    permission=SUPERUSER,
)


@video_matcher.handle()
async def _handle_video(matcher: Matcher, bot: Bot, event: Event, state: T_State) -> None:
    """处理视频解析。"""
    url = str(state.get(STATE_URL_KEY) or "")
    await matcher.send("正在解析视频，请稍候...")
    try:
        result = await parse_url(url)
        video_path = await download_video(result)
    except (ParseError, DownloadError) as exc:
        await matcher.finish(f"解析失败：{exc}")
    except Exception as exc:
        await matcher.finish(f"解析失败：{type(exc).__name__}: {exc}")
    await send_video_result(matcher, bot, event, result, video_path)


@group_toggle_cmd.handle()
async def _handle_group_toggle(matcher: Matcher, event: Event, groups: tuple = RegexGroup()) -> None:
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
