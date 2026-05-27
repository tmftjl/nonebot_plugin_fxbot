"""B 站视频解析与登录。"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any
from collections.abc import Awaitable, Callable

from ....utils.paths import data_dir
from ..types import VideoResult
from .base import ParseError
from .common import COMMON_HEADERS, final_url

COOKIE_PATH: Path = data_dir("video_parser") / "bilibili_cookies.json"
BILI_HEADERS = {
    **COMMON_HEADERS,
    "Referer": "https://www.bilibili.com/",
}

_qr_login: Any | None = None


async def parse(url: str) -> VideoResult:
    """解析 B 站视频。"""
    if "b23.tv" in url:
        url = await final_url(url, headers=BILI_HEADERS)
    matched = re.search(r"(?P<bvid>BV[0-9A-Za-z]{10})", url)
    if not matched:
        raise ParseError("无法识别 B 站 BV 号")
    bvid = matched.group("bvid")
    page_num = 1
    page = re.search(r"[?&]p=(\d{1,3})", url)
    if page:
        page_num = max(1, int(page.group(1)))
    return await _parse_video(bvid, page_num, source=url)


async def _parse_video(bvid: str, page_num: int, *, source: str) -> VideoResult:
    """解析 B 站视频信息和下载流。"""
    try:
        from bilibili_api import request_settings, select_client
        from bilibili_api.video import Video, VideoStreamDownloadURL, AudioStreamDownloadURL, VideoDownloadURLDataDetecter
    except Exception as exc:
        raise ParseError("缺少 bilibili-api-python 依赖，请先安装 requirements.txt") from exc

    select_client("curl_cffi")
    request_settings.set("impersonate", "chrome131")
    credential = await load_credential()
    video = Video(bvid=bvid, credential=credential)
    info = await video.get_info()
    pages = info.get("pages") or []
    page_index = min(max(page_num - 1, 0), max(len(pages) - 1, 0))
    page_info = pages[page_index] if pages else {}
    title = str(page_info.get("part") or info.get("title") or "B站视频")
    duration = float(page_info.get("duration") or info.get("duration") or 0) or None
    cover = page_info.get("first_frame") or info.get("pic")

    download_data = await video.get_download_url(page_index=page_index)
    detector = VideoDownloadURLDataDetecter(download_data)
    streams = detector.detect_best_streams(no_dolby_video=True, no_hdr=True)
    if not streams:
        raise ParseError("B 站没有可下载的视频流")
    video_stream = streams[0]
    if not isinstance(video_stream, VideoStreamDownloadURL):
        raise ParseError("B 站没有可下载的视频流")
    audio_url = None
    if len(streams) > 1 and isinstance(streams[1], AudioStreamDownloadURL):
        audio_url = streams[1].url

    return VideoResult(
        platform="B站",
        title=title,
        video_url=video_stream.url,
        audio_url=audio_url,
        cover_url=cover,
        duration=duration,
        source_url=f"https://www.bilibili.com/video/{bvid}" + (f"?p={page_num}" if page_num > 1 else ""),
        text=str((info.get("owner") or {}).get("name") or ""),
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


async def poll_qrcode(on_scanned: Callable[[str], Awaitable[None]] | None = None) -> str:
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
            COOKIE_PATH.write_text(json.dumps(credential.get_cookies(), ensure_ascii=False, indent=2), encoding="utf-8")
            return "B站登录成功，凭据已保存"
        if any(key in name for key in ("CONF", "SCAN")) and not confirmed:
            confirmed = True
            if on_scanned is not None:
                await on_scanned("二维码已扫描，请在手机上确认登录")
        if any(key in name for key in ("TIMEOUT", "EXPIRED")):
            return "二维码已过期，请重新发送 #B站登录"
        await asyncio.sleep(2)
    return "B站登录超时，请重新发送 #B站登录"
