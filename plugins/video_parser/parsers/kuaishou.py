"""快手视频解析。"""

from __future__ import annotations

from ..types import VideoResult
from .base import ParseError
from .common import COMMON_HEADERS, extract_json, final_url, first, get_text

HEADERS = {**COMMON_HEADERS, "Referer": "https://v.kuaishou.com/"}


async def parse(url: str) -> VideoResult:
    """解析快手视频。"""
    resolved = await final_url(url, headers=HEADERS)
    resolved = resolved.replace("/fw/long-video/", "/fw/photo/")
    html = await get_text(resolved, headers=HEADERS)
    data = extract_json(html, r"window\.INIT_STATE\s*=\s*(.*?)</script>")

    photo = None
    if isinstance(data, dict):
        for item in data.values():
            if isinstance(item, dict) and isinstance(item.get("photo"), dict):
                photo = item["photo"]
                break
    if photo is None:
        raise ParseError("快手页面没有视频信息")

    video_url = _cdn_url(first(photo.get("mainMvUrls")))
    if not video_url:
        raise ParseError("快手页面没有视频直链")

    return VideoResult(
        platform="快手",
        title=str(photo.get("caption") or "快手视频"),
        video_url=video_url,
        cover_url=_cdn_url(first(photo.get("coverUrls"))),
        duration=(float(photo.get("duration")) / 1000) if photo.get("duration") else None,
        source_url=resolved,
        text=str(photo.get("userName") or ""),
        headers=HEADERS.copy(),
    )


def _cdn_url(item: object) -> str | None:
    """提取快手 CDN URL。"""
    if not isinstance(item, dict):
        return None
    value = item.get("url")
    return str(value) if value else None
