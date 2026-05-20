"""点歌命令。"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from nonebot import logger
from nonebot.adapters import Bot, Event
from nonebot.matcher import Matcher
from nonebot.params import RegexGroup
from PIL import Image, ImageDraw

from ...permission import PermLevel, PermScene
from ...plugin import Plugin
from ...utils.compat import build_message, build_message_segment
from ...utils.fonts import load_font
from ...utils.http import get_shared_async_client
from .config import cfg_music

Platform = Literal["qq", "netease"]

P = Plugin("entertain", display_name="娱乐", enabled=True, level=PermLevel.LOW, scene=PermScene.ALL)

_CACHE_TTL = 600
_music_cache: dict[str, tuple[float, tuple[Platform, list["Song"]]]] = {}


@dataclass
class Song:
    """音乐搜索结果。"""

    id: int
    mid: str | None
    vid: str
    song: str
    subtitle: str
    album: str
    singer: str
    cover: str
    pay: str
    time: str
    type: int
    bpm: int
    quality: str
    grp: list["Song"]
    link: str | None = None
    interval: str | None = None
    size: str | None = None
    kbps: str | None = None
    url: str | None = None


def _uid(event: Event) -> str:
    """提取用户 ID。"""
    if hasattr(event, "get_user_id"):
        try:
            return str(event.get_user_id())
        except Exception:
            pass
    return str(getattr(event, "user_id", "") or "")


def _normalize_platform(alias: str | None) -> Platform:
    """归一化音乐平台名称。"""
    if not alias:
        default = str(cfg_music().get("provider_default", "tencent")).lower()
        return "qq" if default == "tencent" else "netease"
    return "qq" if alias.lower() == "qq" else "netease"


def _platform_name_cn(platform: Platform) -> str:
    """返回平台中文名。"""
    return {"qq": "QQ音乐", "netease": "网易云音乐"}[platform]


def _cache_set(user_id: str, value: tuple[Platform, list[Song]]) -> None:
    """写入点歌搜索缓存。"""
    _music_cache[user_id] = (time.time() + _CACHE_TTL, value)


def _cache_get(user_id: str) -> tuple[Platform, list[Song]] | None:
    """读取点歌搜索缓存。"""
    item = _music_cache.get(user_id)
    if item is None:
        return None
    expires_at, value = item
    if expires_at < time.time():
        _music_cache.pop(user_id, None)
        return None
    return value


async def _search_songs_api(platform: Platform, keyword: str) -> list[Song]:
    """调用远端 API 搜索歌曲。"""
    config = cfg_music()
    api_base = str(config.get("api_base") or "https://api.vkeys.cn").rstrip("/")
    num = int(config.get("search_num") or 10)
    url = f"{api_base}/v2/music/tencent/search/song" if platform == "qq" else f"{api_base}/v2/music/netease"

    client = await get_shared_async_client()
    response = await client.get(url, params={"word": keyword, "num": num})
    response.raise_for_status()
    data = response.json()
    if data.get("code") != 200:
        raise RuntimeError(str(data.get("message", "音乐 API 返回错误")))

    items = data.get("data", [])
    if isinstance(items, dict):
        items = [items]

    results: list[Song] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        song_id = int(item.get("id", 0) or 0)
        mid = item.get("mid")
        song_type = int(item.get("type", 0) or 0)
        link = item.get("link")
        if not link:
            if platform == "qq" and mid:
                link = f"https://i.y.qq.com/v8/playsong.html?songmid={mid}&type={song_type}"
            elif platform == "netease" and song_id:
                link = f"https://music.163.com/#/song?id={song_id}"

        results.append(
            Song(
                id=song_id,
                mid=str(mid) if mid else None,
                vid=str(item.get("vid", "") or ""),
                song=str(item.get("song", "未知歌曲") or "未知歌曲"),
                subtitle=str(item.get("subtitle", "") or ""),
                album=str(item.get("album", "") or ""),
                singer=str(item.get("singer", "未知歌手") or "未知歌手"),
                cover=str(item.get("cover", "") or ""),
                pay=str(item.get("pay", "") or ""),
                time=str(item.get("time", "") or ""),
                type=song_type,
                bpm=int(item.get("bpm", 0) or 0),
                quality=str(item.get("quality", "") or ""),
                grp=[],
                link=str(link) if link else None,
            )
        )
    return results


async def _get_song_url_api(platform: Platform, song: Song) -> str | None:
    """获取歌曲播放地址。"""
    config = cfg_music()
    api_base = str(config.get("api_base") or "https://api.vkeys.cn").rstrip("/")
    quality = int(config.get("quality") or 4)
    if platform == "qq":
        url = f"{api_base}/v2/music/tencent/geturl"
        params: dict[str, object] = {"quality": quality, "type": song.type}
        params["mid" if song.mid else "id"] = song.mid or song.id
    else:
        url = f"{api_base}/v2/music/netease"
        params = {"id": song.id, "quality": quality}

    try:
        client = await get_shared_async_client()
        response = await client.get(url, params=params)
        response.raise_for_status()
        data = response.json()
    except Exception:
        logger.opt(exception=True).warning("[musicshare] 获取音乐链接失败")
        return None
    if data.get("code") != 200:
        logger.warning(f"[musicshare] 获取音乐链接失败: {data.get('message')}")
        return None
    result = data.get("data", {})
    return str(result.get("url") or "") if isinstance(result, dict) else None


def _draw_music_list(platform: Platform, keyword: str, songs: list[Song]) -> bytes:
    """按旧版双列卡片排版绘制搜索结果。"""
    bg_color = (240, 242, 245)
    header_color = (64, 84, 180)
    card_bg = (255, 255, 255)
    text_main = (30, 30, 30)
    text_sub = (100, 100, 100)
    accent = (64, 84, 180)

    padding = 30
    columns = 2
    gap_x = 20
    gap_y = 15
    card_h = 70
    col_w = 400
    font_path = Path(__file__).parent / "resource" / "font.ttf"

    font_title = load_font(font_path, 36)
    font_sub = load_font(font_path, 22)
    font_song = load_font(font_path, 26)
    font_artist = load_font(font_path, 20)
    font_badge = load_font(font_path, 20)
    font_footer = load_font(font_path, 18)

    count = min(len(songs), 20)
    rows = (count + columns - 1) // columns
    header_h = 120
    list_h = rows * card_h + max(rows - 1, 0) * gap_y
    footer_h = 50
    width = padding * 2 + col_w * columns + gap_x * (columns - 1)
    height = header_h + list_h + footer_h + padding

    image = Image.new("RGB", (width, height), bg_color)
    draw = ImageDraw.Draw(image)
    draw.rectangle([(0, 0), (width, header_h)], fill=header_color)
    draw.text((padding, 25), f"搜索结果: {keyword}", font=font_title, fill=(255, 255, 255))
    draw.text(
        (padding, 75),
        f"来源: {_platform_name_cn(platform)} | 共找到 {len(songs)} 首歌曲",
        font=font_sub,
        fill=(220, 220, 255),
    )

    start_y = header_h + 20
    for index, song in enumerate(songs[:20]):
        row = index // columns
        col = index % columns
        x = padding + col * (col_w + gap_x)
        y = start_y + row * (card_h + gap_y)
        draw.rounded_rectangle([(x, y), (x + col_w, y + card_h)], radius=8, fill=card_bg)

        badge_size = 36
        bx = x + 15
        by = y + (card_h - badge_size) // 2
        draw.ellipse([(bx, by), (bx + badge_size, by + badge_size)], fill=bg_color)
        idx_str = str(index + 1)
        bbox = draw.textbbox((0, 0), idx_str, font=font_badge)
        draw.text(
            (bx + (badge_size - (bbox[2] - bbox[0])) / 2, by + (badge_size - (bbox[3] - bbox[1])) / 2 - 2),
            idx_str,
            fill=accent,
            font=font_badge,
        )

        text_x = bx + badge_size + 15
        content_w = col_w - (text_x - x) - 10
        song_name = song.song
        while draw.textlength(song_name, font=font_song) > content_w and len(song_name) > 1:
            song_name = song_name[:-2] + "…"
        draw.text((text_x, y + 12), song_name, fill=text_main, font=font_song)
        draw.text((text_x, y + 42), song.singer, fill=text_sub, font=font_artist)

    footer = "发送 #序号 (如 #1) 即可播放"
    bbox = draw.textbbox((0, 0), footer, font=font_footer)
    draw.text(((width - (bbox[2] - bbox[0])) / 2, height - 30), footer, fill=(150, 150, 150), font=font_footer)

    import io

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


search_matcher = P.on_regex(
    r"^#点歌(?:(qq|网易云|netease))?\s*(.*)$",
    name="search",
    display_name="点歌",
    priority=5,
    block=True,
    level=PermLevel.LOW,
    scene=PermScene.ALL,
)

select_matcher = P.on_regex(
    r"^#(\d{1,2})$",
    name="select",
    display_name="选择歌曲",
    priority=5,
    block=True,
    level=PermLevel.LOW,
    scene=PermScene.ALL,
)


@search_matcher.handle()
async def _handle_search(matcher: Matcher, bot: Bot, event: Event, groups: tuple = RegexGroup()) -> None:
    """搜索歌曲。"""
    alias = str(groups[0] or "") if groups else ""
    keyword = str(groups[1] or "").strip() if groups and len(groups) > 1 else ""
    if not keyword:
        await matcher.finish("请提供关键词，例如：#点歌 晴天")
    platform = _normalize_platform(alias or None)

    try:
        songs = await _search_songs_api(platform, keyword)
    except Exception as exc:
        logger.opt(exception=True).warning("[musicshare] 搜索歌曲失败")
        await matcher.finish(f"搜索出错: {exc}")
    if not songs:
        await matcher.finish(f"在 {_platform_name_cn(platform)} 未找到相关歌曲")

    user_id = _uid(event)
    if not user_id:
        await matcher.finish("无法获取用户 ID")
    _cache_set(user_id, (platform, songs))

    image = _draw_music_list(platform, keyword, songs)
    await matcher.finish(build_message(bot, build_message_segment(bot, "image", image)))


@select_matcher.handle()
async def _handle_select(matcher: Matcher, bot: Bot, event: Event, groups: tuple = RegexGroup()) -> None:
    """播放搜索结果中的歌曲。"""
    user_id = _uid(event)
    cached = _cache_get(user_id)
    if not cached:
        await matcher.finish("点歌会话已过期，请重新搜索")
    platform, songs = cached
    index = int(groups[0]) - 1
    if not (0 <= index < len(songs)):
        await matcher.finish("序号超出范围")
    song = songs[index]
    await _send_song(matcher, bot, platform, song)


async def _send_song(matcher: Matcher, bot: Bot, platform: Platform, song: Song) -> None:
    """发送歌曲语音。"""
    audio_url = await _get_song_url_api(platform, song)
    if not audio_url:
        await matcher.finish(f"播放失败：{song.song} - {song.singer}")
    segment = build_message_segment(bot, "record", audio_url)
    await matcher.finish(build_message(bot, segment))


async def _cleanup_cache_loop() -> None:
    """定期清理过期点歌缓存。"""
    while True:
        await asyncio.sleep(300)
        now = time.time()
        for key, (expires_at, _) in list(_music_cache.items()):
            if expires_at < now:
                _music_cache.pop(key, None)
