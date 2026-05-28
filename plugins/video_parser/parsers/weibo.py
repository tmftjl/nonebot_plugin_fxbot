"""微博视频解析。"""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from math import ceil
from time import time
from typing import Any
from uuid import uuid4

import httpx

from ..types import VideoResult
from .base import ParseError
from .common import COMMON_HEADERS, final_url, proxy, timeout

HEADERS = {
    **COMMON_HEADERS,
    "accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,"
        "image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9"
    ),
    "Referer": "https://weibo.com/",
}


async def parse(url: str) -> VideoResult:
    """解析微博视频或图文。"""
    if "mapp.api.weibo.cn/fx/" in url:
        resolved = await final_url(url, headers=HEADERS)
        if resolved == url:
            raise ParseError("微博短链无法跳转")
        return await parse(resolved)

    article = re.search(r"ttarticle/.+?id=(?P<id>\d+)", url) or re.search(r"article/.+?/id/(?P<id>\d+)", url)
    if article:
        return await _parse_article(article.group("id"), source=url)

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


async def _parse_article(article_id: str, *, source: str) -> VideoResult:
    """解析微博文章图文。"""
    params = {
        "_rid": str(uuid4()),
        "id": article_id,
        "_t": int(time() * 1000),
    }
    async with httpx.AsyncClient(timeout=timeout(), proxy=proxy(), verify=False) as client:
        response = await client.get("https://card.weibo.com/article/m/aj/detail", params=params, headers=HEADERS)
        response.raise_for_status()
        payload = response.json()

    if payload.get("msg") != "success":
        raise ParseError("微博文章请求失败")
    data = payload.get("data") or {}
    parsed = _ArticleContentParser()
    parsed.feed(str(data.get("content") or ""))
    image_urls = parsed.image_urls
    if not image_urls:
        raise ParseError("微博文章没有可发送的媒体")
    user = data.get("userinfo") or {}
    return VideoResult(
        platform="微博",
        title=str(data.get("title") or (parsed.text[:40] if parsed.text else "微博文章")),
        image_urls=image_urls,
        cover_url=image_urls[0],
        source_url=str(data.get("url") or source),
        text=str(user.get("screen_name") or ""),
        headers=HEADERS.copy(),
    )


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
    user = data.get("user") or {}
    title = page.get("title") or _strip_html(str(data.get("text") or ""))[:40] or "微博视频"
    cover = (page.get("page_pic") or {}).get("url")

    if not video_url:
        image_urls = _image_urls(data.get("pics") or [])
        if image_urls:
            return VideoResult(
                platform="微博",
                title=str(title or "微博图文"),
                image_urls=image_urls,
                cover_url=_normalize_scheme(cover) or image_urls[0],
                source_url=source,
                text=str(user.get("screen_name") or ""),
                headers=HEADERS.copy(),
            )
        if isinstance(data.get("retweeted_status"), dict):
            return _collect_status(data["retweeted_status"], source=source)
        raise ParseError("该微博没有可发送的媒体")

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


class _ArticleContentParser(HTMLParser):
    """提取微博文章中的段落和图片。"""

    def __init__(self) -> None:
        super().__init__()
        self.image_urls: list[str] = []
        self._paragraphs: list[str] = []
        self._current: list[str] = []
        self._in_paragraph = False

    @property
    def text(self) -> str:
        """返回文章纯文本。"""
        return "\n".join(self._paragraphs).strip()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """处理开始标签。"""
        if tag == "p":
            self._in_paragraph = True
            self._current = []
            return
        if tag != "img":
            return
        attr_map = dict(attrs)
        src = _normalize_scheme(attr_map.get("src"))
        if src:
            self.image_urls.append(src)

    def handle_data(self, data: str) -> None:
        """处理文本内容。"""
        if self._in_paragraph:
            self._current.append(data)

    def handle_endtag(self, tag: str) -> None:
        """处理结束标签。"""
        if tag != "p" or not self._in_paragraph:
            return
        text = html.unescape("".join(self._current)).replace("\u200b", "").strip()
        if text:
            self._paragraphs.append(text)
        self._current = []
        self._in_paragraph = False


def _image_urls(pics: list[object]) -> list[str]:
    """提取微博图片。"""
    urls: list[str] = []
    for pic in pics:
        if not isinstance(pic, dict):
            continue
        large = pic.get("large")
        url = large.get("url") if isinstance(large, dict) else None
        url = url or pic.get("url")
        normalized = _normalize_scheme(url)
        if normalized:
            urls.append(normalized)
    return urls


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
