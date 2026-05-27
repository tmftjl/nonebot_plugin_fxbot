"""小红书视频解析。"""

from __future__ import annotations

import re
from typing import Any

from ..types import VideoResult
from .base import ParseError
from .common import COMMON_HEADERS, extract_json, final_url, first, get_text

HEADERS = {
    **COMMON_HEADERS,
    "Referer": "https://www.xiaohongshu.com/",
}


async def parse(url: str) -> VideoResult:
    """解析小红书视频。"""
    resolved = await final_url(url, headers=HEADERS) if "xhslink.com" in url else url
    matched = re.search(r"(?:explore|discovery/item)/(?P<id>[0-9a-zA-Z]+)", resolved)
    if not matched:
        raise ParseError("无法识别小红书笔记 ID")

    note_id = matched.group("id")
    try:
        return await _parse_explore(resolved, note_id)
    except Exception:
        return await _parse_discovery(resolved)


async def _parse_explore(url: str, note_id: str) -> VideoResult:
    """解析 explore 页面。"""
    html = await get_text(url, headers=HEADERS)
    state = extract_json(html, r"window\.__INITIAL_STATE__=(.*?)</script>", undefined_to_null=True)
    detail_map = (((state.get("note") or {}).get("noteDetailMap")) or {})
    note = ((detail_map.get(note_id) or {}).get("note")) or {}
    if not note:
        raise ParseError("小红书页面没有笔记信息")
    return _result_from_note(note, source_url=url, discovery=False)


async def _parse_discovery(url: str) -> VideoResult:
    """解析 discovery 页面。"""
    html = await get_text(url, headers=HEADERS)
    state = extract_json(html, r"window\.__INITIAL_STATE__=(.*?)</script>", undefined_to_null=True)
    container = state.get("noteData") or {}
    note = (((container.get("data") or {}).get("noteData")) or {})
    preload = container.get("normalNotePreloadData") or {}
    result = _result_from_note(note, source_url=url, discovery=True)
    images = preload.get("imagesList") or []
    if images and not result.cover_url:
        image = first(images)
        if isinstance(image, dict):
            result.cover_url = image.get("urlSizeLarge") or image.get("url")
    return result


def _result_from_note(note: dict[str, Any], *, source_url: str, discovery: bool) -> VideoResult:
    """从笔记结构构造结果。"""
    if note.get("type") != "video" or not isinstance(note.get("video"), dict):
        raise ParseError("该小红书笔记不是视频")
    video_url, duration = _video_url_and_duration(note["video"])
    if not video_url:
        raise ParseError("小红书页面没有视频直链")

    user = note.get("user") or {}
    images = note.get("imageList") or []
    cover = None
    image = first(images)
    if isinstance(image, dict):
        cover = image.get("urlDefault") or image.get("urlSizeLarge") or image.get("url")

    nickname = user.get("nickname") or user.get("nickName") or ""
    timestamp = note.get("time") if discovery else None
    return VideoResult(
        platform="小红书",
        title=str(note.get("title") or note.get("desc") or "小红书视频"),
        video_url=video_url,
        cover_url=cover,
        duration=duration,
        source_url=source_url,
        text=str(nickname or timestamp or ""),
        headers=HEADERS.copy(),
    )


def _video_url_and_duration(video: dict[str, Any]) -> tuple[str | None, float | None]:
    """提取小红书视频流。"""
    stream = (((video.get("media") or {}).get("stream")) or {})
    for key in ("h265", "h264", "av1", "h266"):
        items = stream.get(key)
        item = first(items)
        if isinstance(item, dict) and item.get("masterUrl"):
            duration = item.get("duration")
            return str(item["masterUrl"]), (float(duration) / 1000) if duration else None
    return None, None
