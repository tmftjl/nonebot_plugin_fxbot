"""抖音视频解析。"""

from __future__ import annotations

import asyncio
import re
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

from ..config import cfg_douyin
from ..types import VideoResult
from .base import ParseError
from .common import (
    ANDROID_HEADERS,
    COMMON_HEADERS,
    IOS_HEADERS,
    extract_json,
    final_url,
    first,
    get_text,
    proxy,
    timeout,
)

DOUYIN_API_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.douyin.com/",
}


async def parse(url: str) -> VideoResult:
    """解析抖音视频。"""
    resolved = (
        await final_url(url, headers=await _douyin_request_headers(COMMON_HEADERS))
        if "v.douyin.com" in url or "jx.douyin.com" in url
        else url
    )
    matched = re.search(r"(?P<ty>video|note|slides)/(?P<vid>\d+)", resolved)
    if not matched:
        raise ParseError("无法识别抖音视频 ID")

    ty, vid = matched.group("ty"), matched.group("vid")
    if ty == "slides":
        return await _parse_slides(vid, source_url=resolved)
    for page_url in (
        f"https://m.douyin.com/share/{ty}/{vid}",
        f"https://www.iesdouyin.com/share/{ty}/{vid}",
    ):
        try:
            return await _parse_page(page_url, source_url=resolved)
        except (ParseError, httpx.HTTPError):
            continue
    # 分享页已逐步移除 videoInfoRes，使用网页详情接口兜底。
    return await _parse_detail_api(vid, source_url=resolved)


async def _parse_page(url: str, *, source_url: str) -> VideoResult:
    """解析抖音分享页。"""
    html = await get_text(
        url, headers=await _douyin_request_headers(IOS_HEADERS), follow_redirects=False
    )
    router = extract_json(html, r"window\._ROUTER_DATA\s*=\s*(.*?)</script>")
    # 新版页面可能把作品数据直接放在 loaderData 或其它字段中。
    if item := _find_item(router):
        return _build_result(item, source_url=source_url)
    loader = router.get("loaderData") or {}
    page = loader.get("video_(id)/page") or loader.get("note_(id)/page") or {}
    item_list = ((page.get("videoInfoRes") or {}).get("item_list")) or []
    item = first(item_list)
    if not isinstance(item, dict):
        raise ParseError("抖音页面没有视频信息")

    return _build_result(item, source_url=source_url)


def _find_item(data: Any) -> dict[str, Any] | None:
    """从新版路由数据中寻找作品对象。"""
    if isinstance(data, dict):
        for key in ("aweme_detail", "awemeDetail"):
            value = data.get(key)
            if isinstance(value, dict) and (value.get("video") or value.get("images")):
                return value
        items = data.get("item_list") or data.get("aweme_list") or data.get("aweme_details")
        if isinstance(items, list):
            item = first(items)
            if isinstance(item, dict):
                return item
        for value in data.values():
            if found := _find_item(value):
                return found
    elif isinstance(data, list):
        for value in data:
            if found := _find_item(value):
                return found
    return None


def _build_result(item: dict[str, Any], *, source_url: str) -> VideoResult:
    """把抖音作品对象转换为统一结果。"""
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
            headers=IOS_HEADERS.copy(),
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
        headers=IOS_HEADERS.copy(),
    )


async def _parse_detail_api(video_id: str, *, source_url: str) -> VideoResult:
    """通过抖音网页详情接口获取作品数据。"""
    api_url = "https://www.douyin.com/aweme/v1/web/aweme/detail/"
    params = {
        "device_platform": "webapp",
        "aid": "6383",
        "channel": "channel_pc_web",
        "aweme_id": video_id,
        "pc_client_type": "1",
        "version_code": "190500",
        "version_name": "19.5.0",
        "cookie_enabled": "true",
        "screen_width": "1344",
        "screen_height": "756",
        "browser_language": "zh-CN",
        "browser_platform": "Win32",
        "browser_name": "Firefox",
        "browser_version": "118.0",
        "browser_online": "true",
        "engine_name": "Gecko",
        "engine_version": "109.0",
        "os_name": "Windows",
        "os_version": "10",
        "cpu_core_num": "16",
        "device_memory": "",
        "platform": "PC",
    }
    headers = await _douyin_request_headers(DOUYIN_API_HEADERS)
    query = urlencode(params)
    signature = await _generate_a_bogus(query, headers["User-Agent"])
    params["a_bogus"] = signature
    async with httpx.AsyncClient(timeout=timeout(), proxy=proxy(), verify=False) as client:
        response = await client.get(api_url, params=params, headers=headers)
        response.raise_for_status()
        if not response.content:
            raise ParseError("抖音详情接口未返回数据")
        try:
            data = response.json()
        except ValueError as exc:
            raise ParseError("抖音详情接口返回无效数据") from exc
    item = _find_item(data)
    if not item:
        raise ParseError("抖音详情接口没有作品信息")
    return _build_result(item, source_url=source_url)


async def _douyin_request_headers(base: dict[str, str]) -> dict[str, str]:
    """按配置选择登录 Cookie 或匿名 ttwid。"""
    headers = base.copy()
    settings = cfg_douyin()
    cookie = str(settings.get("cookie") or "").strip()
    if bool(settings.get("use_cookie")):
        if not cookie:
            raise ParseError("已启用抖音 Cookie，但 Cookie 配置为空")
        headers["Cookie"] = cookie
        return headers

    async with httpx.AsyncClient(timeout=timeout(), proxy=proxy(), verify=False) as client:
        response = await client.post(
            "https://ttwid.bytedance.com/ttwid/union/register/",
            json={
                "aid": 1768,
                "union": True,
                "needFid": False,
                "region": "cn",
                "cbUrlProtocol": "https",
                "service": "www.ixigua.com",
                "migrate_info": {"ticket": "", "source": "node"},
            },
            headers={"User-Agent": headers.get("User-Agent", COMMON_HEADERS["User-Agent"])},
        )
        response.raise_for_status()
        ttwid = response.cookies.get("ttwid")
    if not ttwid:
        raise ParseError("抖音匿名 ttwid 获取失败")
    headers["Cookie"] = f"ttwid={ttwid}"
    return headers


async def _generate_a_bogus(query: str, user_agent: str) -> str:
    """调用 R 插件同源算法生成动态 a_bogus。"""
    script = Path(__file__).with_name("a_bogus.cjs")

    def run() -> str:
        result = subprocess.run(
            [
                "node",
                "-e",
                "const a=require(process.argv[1]); process.stdout.write(a.generate_a_bogus(process.argv[2], process.argv[3]));",
                str(script),
                query,
                user_agent,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode or not result.stdout.strip():
            raise RuntimeError(result.stderr.strip() or "a_bogus 生成失败")
        return result.stdout.strip()

    try:
        return await asyncio.to_thread(run)
    except FileNotFoundError as exc:
        raise ParseError("生成抖音签名需要安装 Node.js") from exc
    except RuntimeError as exc:
        raise ParseError(str(exc)) from exc


async def _parse_slides(video_id: str, *, source_url: str) -> VideoResult:
    """解析抖音 slides 图文接口。"""
    url = "https://www.iesdouyin.com/web/api/v2/aweme/slidesinfo/"
    params = {
        "aweme_ids": f"[{video_id}]",
        "request_source": "200",
    }
    async with httpx.AsyncClient(timeout=timeout(), proxy=proxy(), verify=False) as client:
        response = await client.get(url, params=params, headers=ANDROID_HEADERS)
        response.raise_for_status()
        data = response.json()

    item = first(data.get("aweme_details"))
    if not isinstance(item, dict):
        raise ParseError("抖音图文接口没有作品信息")

    dynamic_urls = _dynamic_urls(item.get("images") or [])
    if dynamic_urls:
        video = item.get("video") or {}
        cover = video.get("cover") or {}
        author = item.get("author") or {}
        return VideoResult(
            platform="抖音",
            title=str(item.get("desc") or "抖音图文"),
            video_url=dynamic_urls[0],
            cover_url=first(cover.get("url_list")),
            source_url=source_url,
            text=str(author.get("nickname") or ""),
            headers=ANDROID_HEADERS.copy(),
        )

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
        headers=ANDROID_HEADERS.copy(),
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


def _dynamic_urls(images: list[object]) -> list[str]:
    """提取抖音 slides 动图视频。"""
    urls: list[str] = []
    for image in images:
        if not isinstance(image, dict):
            continue
        video = image.get("video")
        if not isinstance(video, dict):
            continue
        play_addr = video.get("play_addr") or {}
        url = first(play_addr.get("url_list"))
        if url:
            urls.append(str(url).replace("playwm", "play"))
    return urls
