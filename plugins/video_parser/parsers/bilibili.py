"""B 站视频解析与登录。"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from nonebot import logger

from ....utils.paths import data_dir
from ..types import VideoResult
from .base import ParseError
from .common import COMMON_HEADERS, redirect_url

COOKIE_PATH: Path = data_dir("video_parser") / "bilibili_cookies.json"
BILI_HEADERS = {
    **COMMON_HEADERS,
    "Referer": "https://www.bilibili.com/",
}
BILI_MAX_VIDEO_QUALITY = 80
VIDEO_CODEC_RANK = {
    "avc": 3,
    "av01": 2,
    "hev": 1,
}

_qr_login: Any | None = None


async def parse(url: str) -> VideoResult:
    """解析 B 站视频或图文。"""
    if "b23.tv" in url or "bili2233.cn" in url:
        url = await redirect_url(url, headers=BILI_HEADERS)
    matched = re.search(r"(?P<bvid>BV[0-9A-Za-z]{10})", url)
    if matched:
        bvid = matched.group("bvid")
        page_num = 1
        page = re.search(r"[?&]p=(\d{1,3})", url)
        if page:
            page_num = max(1, int(page.group(1)))
        return await _parse_video(
            bvid=bvid, page_num=page_num, source=_clean_video_source(bvid, page_num)
        )

    av = re.search(
        r"(?:bilibili\.com(?:/video)?/)?av(?P<avid>\d{6,})(?:.*?[?&]p=(?P<page>\d{1,3}))?",
        url,
        re.I,
    )
    if av:
        page_num = max(1, int(av.group("page") or 1))
        avid = int(av.group("avid"))
        return await _parse_video(
            avid=avid, page_num=page_num, source=_clean_av_source(avid, page_num)
        )

    dynamic = re.search(r"(?:bilibili\.com/(?:opus|dynamic)/|t\.bilibili\.com/)(?P<id>\d+)", url)
    if dynamic:
        return await _parse_dynamic_or_opus(int(dynamic.group("id")), source=url)

    read = re.search(r"bilibili\.com/read/cv(?P<id>\d+)", url)
    if read:
        return await _parse_article(int(read.group("id")), source=url)

    raise ParseError("无法识别 B 站链接")


def _setup_client() -> None:
    """按原插件方式设置 bilibili_api HTTP 客户端。"""
    from bilibili_api import request_settings, select_client

    select_client("curl_cffi")
    request_settings.set("impersonate", "chrome131")


def _clean_video_source(bvid: str, page_num: int) -> str:
    """构造干净的视频源地址。"""
    return f"https://www.bilibili.com/video/{bvid}" + (f"?p={page_num}" if page_num > 1 else "")


def _clean_av_source(avid: int, page_num: int) -> str:
    """构造干净的 av 源地址。"""
    return f"https://www.bilibili.com/video/av{avid}" + (f"?p={page_num}" if page_num > 1 else "")


async def _parse_video(
    *, bvid: str | None = None, avid: int | None = None, page_num: int, source: str
) -> VideoResult:
    """解析 B 站视频信息和下载流。"""
    try:
        from bilibili_api.video import (
            AudioStreamDownloadURL,
            Video,
            VideoDownloadURLDataDetecter,
            VideoStreamDownloadURL,
        )
    except Exception as exc:
        raise ParseError("缺少 bilibili-api-python 依赖，请先安装 requirements.txt") from exc

    _setup_client()
    credential = await load_credential()
    video = Video(bvid=bvid, aid=avid, credential=credential)
    info = await video.get_info()
    pages = info.get("pages") or []
    page_index = min(max(page_num - 1, 0), max(len(pages) - 1, 0))
    page_info = pages[page_index] if pages else {}
    base_title = str(info.get("title") or "B站视频")
    part_title = str(page_info.get("part") or "").strip()
    if len(pages) > 1 and part_title and part_title != base_title:
        title = f"{base_title} - {part_title}"
    else:
        title = base_title
    duration = float(page_info.get("duration") or info.get("duration") or 0) or None
    cover = page_info.get("first_frame") or info.get("pic")

    download_data = await video.get_download_url(page_index=page_index)
    if not isinstance(download_data, dict):
        raise ParseError(f"B 站下载接口返回格式异常：{type(download_data).__name__}")
    if not download_data.get("dash") and not download_data.get("durl"):
        response_code = download_data.get("code")
        response_message = str(download_data.get("message") or "").strip()
        logger.warning(
            "[video_parser] B站下载接口未返回流 bvid={} avid={} page={} keys={} code={} message={}",
            bvid,
            avid,
            page_num,
            sorted(str(key) for key in download_data),
            response_code,
            response_message,
        )
        if response_code is not None or response_message:
            detail = f"{response_code}: {response_message}".strip(": ")
            raise ParseError(f"B 站下载接口拒绝请求：{detail}")
        raise ParseError("B 站下载接口未返回 dash/durl 流")
    try:
        detector = VideoDownloadURLDataDetecter(download_data)
        video_url, audio_url = _select_download_urls(
            detector, VideoStreamDownloadURL, AudioStreamDownloadURL
        )
    except ParseError:
        raise
    except Exception as exc:
        # 保留库的原始异常，便于区分接口错误响应、库版本不兼容和流为空。
        logger.opt(exception=True).warning(
            "[video_parser] B站下载流解析失败 bvid={} avid={} page={} error_type={} error={}",
            bvid,
            avid,
            page_num,
            type(exc).__name__,
            str(exc),
        )
        detail = str(exc).strip()
        if detail:
            raise ParseError(f"B 站下载流解析失败：{type(exc).__name__}: {detail}") from exc
        raise ParseError(f"B 站下载流解析失败：{type(exc).__name__}") from exc

    return VideoResult(
        platform="B站",
        title=title,
        video_url=video_url,
        audio_url=audio_url,
        cover_url=cover,
        duration=duration,
        source_url=source,
        text=str((info.get("owner") or {}).get("name") or ""),
        headers=BILI_HEADERS.copy(),
    )


def _select_download_urls(
    detector: Any, video_stream_type: type[Any], audio_stream_type: type[Any]
) -> tuple[str, str | None]:
    """从 bilibili_api 解析结果中选择可下载流。"""
    from bilibili_api.video import VideoCodecs

    # 某些 bilibili-api-python 版本的默认 VideoCodecs.UNKNOWN.value
    # 是不可迭代对象；显式传入已知编码可避免库内部 TypeError。
    streams = detector.detect(
        codecs=[VideoCodecs.AV1, VideoCodecs.AVC, VideoCodecs.HEV],
        no_dolby_video=True,
        no_hdr=True,
    )
    video_streams = [
        stream
        for stream in streams
        if isinstance(stream, video_stream_type) and _stream_url(stream)
    ]
    if video_streams:
        preferred_video_streams = [
            stream
            for stream in video_streams
            if 0 < _enum_int(getattr(stream, "video_quality", None)) <= BILI_MAX_VIDEO_QUALITY
        ]
        video_stream = max(preferred_video_streams or video_streams, key=_video_stream_rank)
        video_url = _stream_url(video_stream)
        if not video_url:
            raise ParseError("B 站没有可下载的视频流")
        audio_streams = [
            stream
            for stream in streams
            if isinstance(stream, audio_stream_type) and _stream_url(stream)
        ]
        audio_stream = max(audio_streams, key=_audio_stream_rank) if audio_streams else None
        return video_url, _stream_url(audio_stream) if audio_stream is not None else None

    merged_streams = [stream for stream in streams if _stream_url(stream)]
    if merged_streams:
        merged_stream = max(merged_streams, key=_merged_stream_rank)
        merged_url = _stream_url(merged_stream)
        if merged_url:
            return merged_url, None
    raise ParseError("B 站没有可下载的视频流")


def _stream_url(stream: Any) -> str | None:
    """读取 bilibili_api 流对象的 URL。"""
    url = getattr(stream, "url", None)
    return str(url) if url else None


def _video_stream_rank(stream: Any) -> tuple[int, int]:
    """给视频流排序，允许依赖返回未知编码。"""
    codec = getattr(stream, "video_codecs", None)
    codec_value = str(getattr(codec, "value", "") or "").lower()
    return (
        _enum_int(getattr(stream, "video_quality", None)),
        VIDEO_CODEC_RANK.get(codec_value, 0),
    )


def _audio_stream_rank(stream: Any) -> tuple[int, int]:
    """给音频流排序。"""
    quality = getattr(stream, "audio_quality", None)
    quality_name = str(getattr(quality, "name", "") or "")
    special_rank = 2 if quality_name == "DOLBY" else 1 if quality_name == "HI_RES" else 0
    return special_rank, _enum_int(quality)


def _merged_stream_rank(stream: Any) -> int:
    """给 FLV/MP4 合并流排序。"""
    return _enum_int(getattr(stream, "video_quality", None))


def _enum_int(value: Any) -> int:
    """读取枚举或原始数值的整数值。"""
    raw = getattr(value, "value", value)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


async def _parse_dynamic_or_opus(dynamic_id: int, *, source: str) -> VideoResult:
    """解析 B 站动态或图文。"""
    try:
        from bilibili_api.dynamic import Dynamic
    except Exception as exc:
        raise ParseError("缺少 bilibili-api-python 依赖，请先安装 requirements.txt") from exc

    _setup_client()
    dynamic = Dynamic(dynamic_id, await load_credential())
    try:
        if await dynamic.is_article():
            return await _parse_bili_opus(await _maybe_await(dynamic.turn_to_opus()), source=source)
        info = await dynamic.get_info()
    except Exception as exc:
        raise ParseError("B站动态解析失败") from exc
    return await _result_from_dynamic((info.get("item") or info), source=source)


async def _parse_article(read_id: int, *, source: str) -> VideoResult:
    """解析 B 站专栏转图文。"""
    try:
        from bilibili_api.article import Article
    except Exception as exc:
        raise ParseError("缺少 bilibili-api-python 依赖，请先安装 requirements.txt") from exc

    _setup_client()
    article = Article(read_id)
    return await _parse_bili_opus(await _maybe_await(article.turn_to_opus()), source=source)


async def _maybe_await(value: Any) -> Any:
    """兼容 bilibili_api 不同对象的同步/异步返回。"""
    if inspect.isawaitable(value):
        return await value
    return value


async def _parse_bili_opus(opus: Any, *, source: str) -> VideoResult:
    """解析 B 站图文动态。"""
    try:
        info = await opus.get_info()
    except Exception as exc:
        raise ParseError("B站图文解析失败") from exc
    item = info.get("item") or {}
    modules = item.get("modules") or []
    title = ((item.get("basic") or {}).get("title")) or "B站图文"
    text_parts: list[str] = []
    image_urls: list[str] = []
    author = ""
    for module in modules:
        if module.get("module_author"):
            author = str((module.get("module_author") or {}).get("name") or "")
        content = module.get("module_content") or {}
        for paragraph in content.get("paragraphs") or []:
            text = paragraph.get("text") or {}
            nodes = text.get("nodes") or []
            words = "".join(str(((node.get("word") or {}).get("words")) or "") for node in nodes)
            if words.strip():
                text_parts.append(words.strip())
            pic = paragraph.get("pic") or {}
            for image in pic.get("pics") or []:
                if isinstance(image, dict) and image.get("url"):
                    image_urls.append(str(image["url"]))
    if not image_urls:
        raise ParseError("B站图文没有图片")
    return VideoResult(
        platform="B站",
        title=str(title or (text_parts[0][:40] if text_parts else "B站图文")),
        image_urls=image_urls,
        cover_url=image_urls[0],
        source_url=source,
        text=author,
        headers=BILI_HEADERS.copy(),
    )


async def _result_from_dynamic(item: dict[str, Any], *, source: str) -> VideoResult:
    """从 B 站动态结构构造解析结果。"""
    modules = item.get("modules") or {}
    author = modules.get("module_author") or {}
    dynamic = modules.get("module_dynamic") or {}
    major = dynamic.get("major") or {}
    archive = major.get("archive") or {}
    if archive.get("bvid"):
        return await _parse_video(bvid=str(archive["bvid"]), page_num=1, source=source)
    opus = major.get("opus") or {}
    pics = opus.get("pics") or []
    image_urls = [str(pic["url"]) for pic in pics if isinstance(pic, dict) and pic.get("url")]
    title = (
        opus.get("title")
        or ((opus.get("summary") or {}).get("text"))
        or ((dynamic.get("desc") or {}).get("text"))
        or "B站动态"
    )
    if not image_urls:
        raise ParseError("B站动态没有可发送的媒体")
    return VideoResult(
        platform="B站",
        title=str(title),
        image_urls=image_urls,
        cover_url=image_urls[0],
        source_url=source,
        text=str(author.get("name") or ""),
        headers=BILI_HEADERS.copy(),
    )


async def load_credential() -> Any | None:
    """加载 B 站登录凭据。"""
    if not COOKIE_PATH.exists():
        return None
    try:
        from bilibili_api import Credential

        cookies = json.loads(COOKIE_PATH.read_text(encoding="utf-8"))
        credential = Credential.from_cookies(cookies)
        if await credential.check_valid():
            return credential
    except Exception:
        return None
    return None


async def create_qrcode() -> bytes:
    """创建 B 站登录二维码。"""
    global _qr_login
    try:
        from bilibili_api.login_v2 import QrCodeLogin
    except Exception as exc:
        raise ParseError("缺少 bilibili-api-python 依赖，请先安装 requirements.txt") from exc

    _qr_login = QrCodeLogin()
    await _qr_login.generate_qrcode()
    picture = _qr_login.get_qrcode_picture()
    return bytes(picture.content)


async def poll_qrcode(
    on_scanned: Callable[[str], Awaitable[None]] | None = None,
) -> str:
    """轮询 B 站二维码登录状态并保存凭据。"""
    if _qr_login is None:
        raise ParseError("请先生成 B 站登录二维码")

    confirmed = False
    for _ in range(30):
        state = await _qr_login.check_state()
        name = str(getattr(state, "name", state)).upper()
        if "DONE" in name:
            credential = _qr_login.get_credential()
            COOKIE_PATH.parent.mkdir(parents=True, exist_ok=True)
            COOKIE_PATH.write_text(
                json.dumps(credential.get_cookies(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return "B站登录成功，凭据已保存"
        if any(key in name for key in ("CONF", "SCAN")) and not confirmed:
            confirmed = True
            if on_scanned is not None:
                await on_scanned("二维码已扫描，请在手机上确认登录")
        if any(key in name for key in ("TIMEOUT", "EXPIRED")):
            return "二维码已过期，请重新发送 #B站登录"
        await asyncio.sleep(2)
    return "B站登录超时，请重新发送 #B站登录"
