"""微博视频解析。"""

from __future__ import annotations

import html
import re
from math import ceil
from time import time
from typing import Any

import httpx

from ..types import VideoResult
from .base import ParseError
from .common import COMMON_HEADERS, proxy, timeout

HEADERS = {
    **COMMON_HEADERS,
    "Referer": "https://weibo.com/",
}


async def parse(url: str) -> VideoResult:
    """解析微博视频。"""
    tv = re.search(r"weibo\.com/tv/show/\d{4}:\d+\?mid=(?P<mid>\d+)", url)
    if tv:
        return await _parse_status(_mid2id(tv.group("mid")), source=url)

    fid = re.search(r"video\.weibo\.com/show\?fid=(?P<fid>\d+:\d+)", url)
    if fid:
        return await _parse_fid(fid.group("fid"), source=url)

    status = re.search(r"(?:weibo\.cn/(?:status|detail|\d+)/|weibo\.com/\d+/)(?P<wid>[0-9A-Za-z]+)", url)
    if status:
        return await _parse_status(status.group("wid"), source=url)

    raise ParseError("无法识别微博视频链接")


async def _parse_fid(fid: str, *, source: str) -> VideoResult:
    """解析微博视频页。"""
    req_url = f"https://h5.video.weibo.com/api/component?page=/show/{fid}"
    headers = {
        **HEADERS,
        "Referer": f"https://h5.video.weibo.com/show/{fid}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    content = 'data={"Component_Play_Playinfo":{"oid":"' + fid + '"}}'
    async with httpx.AsyncClient(timeout=timeout(), proxy=proxy(), verify=False) as client:
        response = await client.post(req_url, headers=headers, content=content)
        response.raise_for_status()
        data = response.json()
    play = ((data.get("data") or {}).get("Component_Play_Playinfo")) or {}
    video_url = _normalize_scheme(next(iter((play.get("urls") or {}).values()), None) or play.get("stream_url"))
    if not video_url:
        raise ParseError("微博视频页没有视频直链")
    return VideoResult(
        platform="微博",
        title=str(play.get("title") or "微博视频"),
        video_url=video_url,
        cover_url=_normalize_scheme(play.get("cover_image")),
        duration=float(play.get("duration_time")) if play.get("duration_time") else None,
        source_url=source,
        text=_strip_html(str(play.get("text") or "")),
        headers=HEADERS.copy(),
    )


async def _parse_status(wid: str, *, source: str) -> VideoResult:
    """解析微博状态。"""
    headers = {
        **HEADERS,
        "accept": "application/json, text/plain, */*",
        "referer": f"https://m.weibo.cn/detail/{wid}",
        "origin": "https://m.weibo.cn",
        "x-requested-with": "XMLHttpRequest",
        "mweibo-pwa": "1",
    }
    url = f"https://m.weibo.cn/statuses/show?id={wid}&_={int(time() * 1000)}"
    async with httpx.AsyncClient(timeout=timeout(), proxy=proxy(), follow_redirects=False, cookies={}, verify=False) as client:
        response = await client.get(url, headers=headers)
        if response.status_code != 200:
            raise ParseError(f"微博接口返回 {response.status_code}")
        data = response.json().get("data") or {}
    return _collect_status(data, source=source)


def _collect_status(data: dict[str, Any], *, source: str) -> VideoResult:
    """从微博状态构造结果。"""
    page = data.get("page_info") or {}
    media = page.get("media_info") or {}
    urls = page.get("urls") or {}
    video_url = urls.get("mp4_720p_mp4") or urls.get("mp4_hd_mp4") or urls.get("mp4_ld_mp4")
    video_url = video_url or media.get("stream_url") or media.get("stream_urls_hd")
    if not video_url and isinstance(data.get("retweeted_status"), dict):
        return _collect_status(data["retweeted_status"], source=source)
    if not video_url:
        raise ParseError("该微博没有视频")

    user = data.get("user") or {}
    title = page.get("title") or _strip_html(str(data.get("text") or ""))[:40] or "微博视频"
    cover = (page.get("page_pic") or {}).get("url")
    return VideoResult(
        platform="微博",
        title=str(title),
        video_url=_normalize_scheme(video_url) or str(video_url),
        cover_url=_normalize_scheme(cover),
        duration=float(media.get("duration")) if media.get("duration") else None,
        source_url=source,
        text=str(user.get("screen_name") or ""),
        headers=HEADERS.copy(),
    )


def _strip_html(text: str) -> str:
    """去除微博 HTML 标签。"""
    return html.unescape(re.sub(r"<[^>]*>", "", text.replace("<br />", "\n"))).strip()


def _normalize_scheme(url: object) -> str | None:
    """补全 URL scheme。"""
    if not url:
        return None
    value = str(url)
    if value.startswith("//"):
        return "https:" + value
    return value


def _base62_encode(number: int) -> str:
    """将数字转换为 base62。"""
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    if number == 0:
        return "0"
    result = ""
    while number > 0:
        result = alphabet[number % 62] + result
        number //= 62
    return result


def _mid2id(mid: str) -> str:
    """将微博 mid 转换为短 ID。"""
    rev = str(mid)[::-1]
    parts = []
    for i in range(ceil(len(rev) / 7)):
        part = _base62_encode(int(rev[i * 7 : (i + 1) * 7][::-1]))
        if i < ceil(len(rev) / 7) - 1:
            part = part.rjust(4, "0")
        parts.append(part)
    return "".join(reversed(parts))
