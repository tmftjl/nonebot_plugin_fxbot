"""抖音视频解析。"""

from __future__ import annotations

import re

from ..types import VideoResult
from .base import ParseError
from .common import COMMON_HEADERS, extract_json, final_url, first, get_text


async def parse(url: str) -> VideoResult:
    """解析抖音视频。"""
    resolved = await final_url(url, headers=COMMON_HEADERS) if "v.douyin.com" in url or "jx.douyin.com" in url else url
    matched = re.search(r"(?P<ty>video|note)/(?P<vid>\d+)", resolved)
    if not matched:
        raise ParseError("无法识别抖音视频 ID")

    ty, vid = matched.group("ty"), matched.group("vid")
    for page_url in (f"https://m.douyin.com/share/{ty}/{vid}", f"https://www.iesdouyin.com/share/{ty}/{vid}"):
        try:
            return await _parse_page(page_url, source_url=resolved)
        except ParseError:
            continue
    raise ParseError("抖音视频解析失败")


async def _parse_page(url: str, *, source_url: str) -> VideoResult:
    """解析抖音分享页。"""
    html = await get_text(url, headers=COMMON_HEADERS, follow_redirects=False)
    router = extract_json(html, r"window\._ROUTER_DATA\s*=\s*(.*?)</script>")
    loader = router.get("loaderData") or {}
    page = loader.get("video_(id)/page") or loader.get("note_(id)/page") or {}
    item_list = ((page.get("videoInfoRes") or {}).get("item_list")) or []
    item = first(item_list)
    if not isinstance(item, dict):
        raise ParseError("抖音页面没有视频信息")

    video = item.get("video") or {}
    play_addr = video.get("play_addr") or {}
    video_url = first(play_addr.get("url_list"))
    if not video_url:
        raise ParseError("抖音页面没有视频直链")
    video_url = str(video_url).replace("playwm", "play")

    cover = video.get("cover") or {}
    author = item.get("author") or {}
    return VideoResult(
        platform="抖音",
        title=str(item.get("desc") or "抖音视频"),
        video_url=video_url,
        cover_url=first(cover.get("url_list")),
        duration=(float(video.get("duration")) / 1000) if video.get("duration") else None,
        source_url=source_url,
        text=str(author.get("nickname") or ""),
        headers=COMMON_HEADERS.copy(),
    )
