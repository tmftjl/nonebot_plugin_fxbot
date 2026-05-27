"""视频解析下载器。"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from ...utils.paths import cache_dir
from .config import cfg_general, cfg_network
from .types import VideoResult

CACHE_DIR = cache_dir("video_parser")


class DownloadError(RuntimeError):
    """下载失败。"""


def _suffix_from_url(url: str, default: str) -> str:
    """从 URL 推断文件后缀。"""
    suffix = Path(urlsplit(url).path).suffix.lower()
    if suffix and len(suffix) <= 8:
        return suffix
    return default


def _cache_path(url: str, suffix: str) -> Path:
    """生成缓存路径。"""
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()
    return CACHE_DIR / f"{digest}{suffix}"


def _timeout() -> httpx.Timeout:
    """构造请求超时。"""
    seconds = float(cfg_general().get("request_timeout_seconds", 20))
    return httpx.Timeout(seconds)


def _proxy() -> str | None:
    """读取代理地址。"""
    value = str(cfg_network().get("proxy") or "").strip()
    return value or None


async def download_file(url: str, *, suffix: str = ".mp4", headers: dict[str, str] | None = None) -> Path:
    """下载远程文件到缓存目录。"""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    file_path = _cache_path(url, _suffix_from_url(url, suffix))
    if file_path.exists() and file_path.stat().st_size > 0:
        return file_path

    max_bytes = int(cfg_general().get("max_file_mb", 80)) * 1024 * 1024
    total = 0
    try:
        async with httpx.AsyncClient(timeout=_timeout(), proxy=_proxy(), follow_redirects=True, verify=False) as client:
            async with client.stream("GET", url, headers=headers or {}) as response:
                response.raise_for_status()
                content_length = response.headers.get("Content-Length")
                if content_length and int(content_length) > max_bytes:
                    raise DownloadError(f"视频大小超过限制：{int(content_length) / 1024 / 1024:.1f} MB")
                with file_path.open("wb") as file:
                    async for chunk in response.aiter_bytes(1024 * 1024):
                        total += len(chunk)
                        if total > max_bytes:
                            raise DownloadError(f"视频大小超过限制：{max_bytes // 1024 // 1024} MB")
                        file.write(chunk)
    except Exception:
        if file_path.exists():
            file_path.unlink(missing_ok=True)
        raise

    if file_path.stat().st_size == 0:
        file_path.unlink(missing_ok=True)
        raise DownloadError("视频为空文件")
    return file_path


async def _merge_av(video_path: Path, audio_path: Path, output_path: Path) -> Path:
    """使用 ffmpeg 合并音视频。"""
    if output_path.exists() and output_path.stat().st_size > 0:
        return output_path
    process = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(audio_path),
        "-c",
        "copy",
        str(output_path),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    code = await process.wait()
    if code != 0 or not output_path.exists() or output_path.stat().st_size == 0:
        raise DownloadError("ffmpeg 合并音视频失败")
    return output_path


async def download_video(result: VideoResult) -> Path:
    """下载解析结果中的视频。"""
    max_duration = float(cfg_general().get("max_duration_seconds", 480))
    if result.duration and result.duration > max_duration:
        raise DownloadError(f"视频时长超过限制：{int(result.duration)} 秒")

    if not result.audio_url:
        return await download_file(result.video_url, suffix=".mp4", headers=result.headers)

    video_path, audio_path = await asyncio.gather(
        download_file(result.video_url, suffix=".m4s", headers=result.headers),
        download_file(result.audio_url, suffix=".m4a", headers=result.headers),
    )
    output = CACHE_DIR / f"{hashlib.sha1((result.video_url + result.audio_url).encode('utf-8')).hexdigest()}.mp4"
    return await _merge_av(video_path, audio_path, output)
