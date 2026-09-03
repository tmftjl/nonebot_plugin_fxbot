"""快手视频解析。"""

from __future__ import annotations

from ..types import VideoResult
from .base import ParseError
from .common import IOS_HEADERS, extract_json, final_url, first, get_text

HEADERS = {**IOS_HEADERS, "Referer": "https://v.kuaishou.com/"}


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
        image_urls = _atlas_urls(photo)
        if not image_urls:
            raise ParseError("快手页面没有可发送的媒体")
        return VideoResult(
            platform="快手",
            title=str(photo.get("caption") or "快手图集"),
            image_urls=image_urls,
            cover_url=image_urls[0],
            source_url=resolved,
            text=str(photo.get("userName") or ""),
            headers=HEADERS.copy(),
        )

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


def _atlas_urls(photo: dict[str, object]) -> list[str]:
    """提取快手图集图片。"""
    ext_params = photo.get("ext_params") or {}
    if not isinstance(ext_params, dict):
        return []
    atlas = ext_params.get("atlas") or {}
    if not isinstance(atlas, dict):
        return []
    cdn_items = atlas.get("cdnList") or []
    routes = atlas.get("list") or []
    cdn_item = first(cdn_items)
    if not isinstance(cdn_item, dict) or not isinstance(routes, list):
        return []
    cdn = str(cdn_item.get("cdn") or "").strip("/")
    if not cdn:
        return []
    return [f"https://{cdn}/{str(route).lstrip('/')}" for route in routes if route]
