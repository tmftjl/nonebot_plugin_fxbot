"""抖音视频解析。"""

from __future__ import annotations

import re

import httpx

from ..types import VideoResult
from .base import ParseError
from .common import COMMON_HEADERS, extract_json, final_url, first, get_text, proxy, timeout


async def parse(url: str) -> VideoResult:
    """解析抖音视频。"""
    resolved = await final_url(url, headers=COMMON_HEADERS) if "v.douyin.com" in url or "jx.douyin.com" in url else url
    matched = re.search(r"(?P<ty>video|note|slides)/(?P<vid>\d+)", resolved)
    if not matched:
        raise ParseError("无法识别抖音视频 ID")

    ty, vid = matched.group("ty"), matched.group("vid")
    if ty == "slides":
        return await _parse_slides(vid, source_url=resolved)
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
    author = item.get("author") or {}
    title = str(item.get("desc") or "抖音内容")

    if not video_url:
        image_urls = _image_urls(item.get("images") or [])
        if not image_urls:
            raise ParseError("抖音页面没有可发送的媒体")
        return VideoResult(
            platform="抖音",
            title=title,
            image_urls=image_urls,
            cover_url=image_urls[0],
            source_url=source_url,
            text=str(author.get("nickname") or ""),
            headers=COMMON_HEADERS.copy(),
        )

    video_url = str(video_url).replace("playwm", "play")
    cover = video.get("cover") or {}
    return VideoResult(
        platform="抖音",
        title=title,
        video_url=video_url,
        cover_url=first(cover.get("url_list")),
        duration=(float(video.get("duration")) / 1000) if video.get("duration") else None,
        source_url=source_url,
        text=str(author.get("nickname") or ""),
        headers=COMMON_HEADERS.copy(),
    )


async def _parse_slides(video_id: str, *, source_url: str) -> VideoResult:
    """解析抖音 slides 图文接口。"""
    url = "https://www.iesdouyin.com/web/api/v2/aweme/slidesinfo/"
    params = {
        "aweme_ids": f"[{video_id}]",
        "request_source": "200",
    }
    async with httpx.AsyncClient(timeout=timeout(), proxy=proxy(), verify=False) as client:
        response = await client.get(url, params=params, headers=COMMON_HEADERS)
        response.raise_for_status()
        data = response.json()

    item = first(data.get("aweme_details"))
    if not isinstance(item, dict):
        raise ParseError("抖音图文接口没有作品信息")

    image_urls = _image_urls(item.get("images") or [])
    if not image_urls:
        raise ParseError("抖音图文接口没有图片")

    author = item.get("author") or {}
    return VideoResult(
        platform="抖音",
        title=str(item.get("desc") or "抖音图文"),
        image_urls=image_urls,
        cover_url=image_urls[0],
        source_url=source_url,
        text=str(author.get("nickname") or ""),
        headers=COMMON_HEADERS.copy(),
    )


def _image_urls(images: list[object]) -> list[str]:
    """提取抖音图文图片。"""
    urls: list[str] = []
    for image in images:
        if not isinstance(image, dict):
            continue
        url = first(image.get("url_list"))
        if url:
            urls.append(str(url))
    return urls
