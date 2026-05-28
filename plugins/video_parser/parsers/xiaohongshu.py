"""小红书媒体解析。"""

from __future__ import annotations

import re
from typing import Any

from ..types import VideoResult
from .base import ParseError
from .common import COMMON_HEADERS, IOS_HEADERS as SHARED_IOS_HEADERS, extract_json, first, get_text, redirect_url

HEADERS = {
    **COMMON_HEADERS,
    "Referer": "https://www.xiaohongshu.com/",
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,"
        "image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7"
    ),
}

IOS_HEADERS = {
    **SHARED_IOS_HEADERS,
    "Referer": "https://www.xiaohongshu.com/",
    "origin": "https://www.xiaohongshu.com",
    "x-requested-with": "XMLHttpRequest",
    "sec-fetch-site": "same-origin",
    "sec-fetch-mode": "cors",
    "sec-fetch-dest": "empty",
}


async def parse(url: str) -> VideoResult:
    """解析小红书视频或图文。"""
    resolved = await redirect_url(url, headers=IOS_HEADERS) if "xhslink.com" in url else url
    matched = re.search(r"(?:explore|discovery/item)/(?P<query>(?P<id>[0-9a-zA-Z]+)(?:\?[^#\s]+)?)", resolved)
    if not matched:
        raise ParseError("无法识别小红书笔记 ID")

    note_id = matched.group("id")
    query = matched.group("query")
    explore_url = f"https://www.xiaohongshu.com/explore/{query}"
    discovery_url = f"https://www.xiaohongshu.com/discovery/item/{query}"
    try:
        return await _parse_explore(explore_url, note_id)
    except Exception:
        return await _parse_discovery(discovery_url)


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
    html = await get_text(url, headers=IOS_HEADERS)
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
    if images and not result.image_urls:
        result.image_urls = _image_urls(images)
    return result


def _result_from_note(note: dict[str, Any], *, source_url: str, discovery: bool) -> VideoResult:
    """从笔记结构构造结果。"""
    user = note.get("user") or {}
    images = note.get("imageList") or []
    cover = None
    image = first(images)
    if isinstance(image, dict):
        cover = image.get("urlDefault") or image.get("urlSizeLarge") or image.get("url")

    nickname = user.get("nickname") or user.get("nickName") or ""
    timestamp = note.get("time") if discovery else None
    title = str(note.get("title") or note.get("desc") or "小红书笔记")

    if note.get("type") == "video" and isinstance(note.get("video"), dict):
        video_urls, duration = _video_urls_and_duration(note["video"])
        if not video_urls:
            raise ParseError("小红书页面没有视频直链")
        return VideoResult(
            platform="小红书",
            title=title,
            video_url=video_urls[0],
            video_urls=video_urls[1:],
            cover_url=cover,
            duration=duration,
            source_url=source_url,
            text=str(nickname or timestamp or ""),
            headers=HEADERS.copy(),
        )

    image_urls = _image_urls(images)
    if not image_urls:
        raise ParseError("该小红书笔记没有可发送的媒体")
    return VideoResult(
        platform="小红书",
        title=title,
        cover_url=cover,
        image_urls=image_urls,
        source_url=source_url,
        text=str(nickname or timestamp or ""),
        headers=HEADERS.copy(),
    )


def _video_urls_and_duration(video: dict[str, Any]) -> tuple[list[str], float | None]:
    """提取小红书视频流和备用地址。"""
    stream = (((video.get("media") or {}).get("stream")) or {})
    urls: list[str] = []
    duration = None
    for key in ("h265", "h264", "av1", "h266"):
        items = stream.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            if duration is None and item.get("duration"):
                duration = float(item["duration"]) / 1000
            if item.get("masterUrl"):
                urls.append(str(item["masterUrl"]))
            backups = item.get("backupUrl")
            if isinstance(backups, list):
                urls.extend(str(url) for url in backups if url)
    return list(dict.fromkeys(urls)), duration


def _image_urls(images: list[Any]) -> list[str]:
    """提取小红书图文图片。"""
    urls: list[str] = []
    for image in images:
        if not isinstance(image, dict):
            continue
        url = image.get("urlDefault") or image.get("urlSizeLarge") or image.get("url")
        if url:
            urls.append(str(url))
    return urls
