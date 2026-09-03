"""视频解析下载器。"""

from __future__ import annotations

import asyncio
import hashlib
import shutil
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import curl_cffi
import httpx

from ...utils.paths import cache_dir
from .config import cfg_general, cfg_network
from .types import VideoResult

CACHE_DIR = cache_dir("video_parser")
LEGACY_MEDIA_SUFFIXES = {
    ".gif",
    ".jpg",
    ".jpeg",
    ".m4a",
    ".m4s",
    ".mp4",
    ".png",
    ".webp",
}
_legacy_cache_cleaned = False


class DownloadError(RuntimeError):
    """下载失败。"""


def _suffix_from_url(url: str, default: str) -> str:
    """从 URL 推断文件后缀。"""
    suffix = Path(urlsplit(url).path).suffix.lower()
    if suffix and len(suffix) <= 8:
        return suffix
    return default


def create_download_dir() -> Path:
    """创建单次解析使用的临时下载目录。"""
    directory = CACHE_DIR / uuid.uuid4().hex
    directory.mkdir(parents=True, exist_ok=False)
    return directory


def cleanup_download_dir(directory: Path) -> None:
    """清理单次解析留下的临时下载目录。"""
    resolved_cache = CACHE_DIR.resolve()
    resolved_directory = directory.resolve()
    if resolved_directory == resolved_cache or resolved_cache not in resolved_directory.parents:
        return
    shutil.rmtree(resolved_directory, ignore_errors=True)


def cleanup_legacy_cache() -> None:
    """清理旧版本直接留在缓存根目录的媒体文件。"""
    global _legacy_cache_cleaned
    if _legacy_cache_cleaned:
        return
    _legacy_cache_cleaned = True
    if not CACHE_DIR.exists():
        return
    for path in CACHE_DIR.iterdir():
        if path.is_file() and path.suffix.lower() in LEGACY_MEDIA_SUFFIXES:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                continue


def _cache_path(url: str, suffix: str, *, directory: Path) -> Path:
    """生成缓存路径。"""
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()
    return directory / f"{digest}{suffix}"


def _timeout() -> httpx.Timeout:
    """构造请求超时。"""
    seconds = float(cfg_general().get("request_timeout_seconds", 20))
    return httpx.Timeout(seconds)


def _proxy() -> str | None:
    """读取代理地址。"""
    value = str(cfg_network().get("proxy") or "").strip()
    return value or None


async def download_file(
    url: str,
    *,
    suffix: str = ".mp4",
    headers: dict[str, str] | None = None,
    directory: Path | None = None,
) -> Path:
    """下载远程文件到缓存目录。"""
    directory = directory or CACHE_DIR
    directory.mkdir(parents=True, exist_ok=True)
    file_path = _cache_path(url, _suffix_from_url(url, suffix), directory=directory)
    if file_path.exists() and file_path.stat().st_size > 0:
        return file_path

    headers = headers or {}
    try:
        return await _download_file_httpx(url, file_path=file_path, headers=headers)
    except DownloadError:
        if file_path.exists():
            file_path.unlink(missing_ok=True)
        raise
    except httpx.HTTPError:
        if file_path.exists():
            file_path.unlink(missing_ok=True)
        try:
            return await _download_file_curl(url, file_path=file_path, headers=headers)
        except Exception as exc:
            if file_path.exists():
                file_path.unlink(missing_ok=True)
            raise DownloadError("媒体下载失败") from exc


async def _download_file_httpx(url: str, *, file_path: Path, headers: dict[str, str]) -> Path:
    """使用 httpx 下载文件。"""
    max_bytes = int(cfg_general().get("max_file_mb", 80)) * 1024 * 1024
    total = 0
    async with httpx.AsyncClient(
        timeout=_timeout(), proxy=_proxy(), follow_redirects=True, verify=False
    ) as client:
        async with client.stream("GET", url, headers=headers) as response:
            response.raise_for_status()
            _check_content_length(response.headers.get("Content-Length"), max_bytes)
            with file_path.open("wb") as file:
                async for chunk in response.aiter_bytes(1024 * 1024):
                    total += len(chunk)
                    _check_total_size(total, max_bytes)
                    file.write(chunk)
    return _ensure_downloaded(file_path)


async def _download_file_curl(url: str, *, file_path: Path, headers: dict[str, str]) -> Path:
    """使用 curl_cffi 下载文件。"""
    max_bytes = int(cfg_general().get("max_file_mb", 80)) * 1024 * 1024
    total = 0
    async with curl_cffi.AsyncSession(allow_redirects=True) as session:
        response = await session.get(
            url,
            headers=headers,
            timeout=float(cfg_general().get("request_timeout_seconds", 20)),
            stream=True,
        )
        response.raise_for_status()
        _check_content_length(response.headers.get("Content-Length"), max_bytes)
        with file_path.open("wb") as file:
            async for chunk in response.aiter_content(chunk_size=1024 * 1024):
                total += len(chunk)
                _check_total_size(total, max_bytes)
                file.write(chunk)
    return _ensure_downloaded(file_path)


def _check_content_length(content_length: str | None, max_bytes: int) -> None:
    """检查响应声明大小。"""
    if content_length and int(content_length) > max_bytes:
        raise DownloadError(f"文件大小超过限制：{int(content_length) / 1024 / 1024:.1f} MB")


def _check_total_size(total: int, max_bytes: int) -> None:
    """检查已下载大小。"""
    if total > max_bytes:
        raise DownloadError(f"文件大小超过限制：{max_bytes // 1024 // 1024} MB")


def _ensure_downloaded(file_path: Path) -> Path:
    """确认文件有效。"""
    if file_path.stat().st_size == 0:
        file_path.unlink(missing_ok=True)
        raise DownloadError("文件为空")
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


async def download_video(result: VideoResult, *, directory: Path | None = None) -> Path:
    """下载解析结果中的视频。"""
    if not result.video_url:
        raise DownloadError("解析结果没有视频直链")
    max_duration = float(cfg_general().get("max_duration_seconds", 480))
    if result.duration and result.duration > max_duration:
        raise DownloadError(f"视频时长超过限制：{int(result.duration)} 秒")

    if not result.audio_url:
        return await download_file(
            result.video_url, suffix=".mp4", headers=result.headers, directory=directory
        )

    video_path, audio_path = await asyncio.gather(
        download_file(result.video_url, suffix=".m4s", headers=result.headers, directory=directory),
        download_file(result.audio_url, suffix=".m4a", headers=result.headers, directory=directory),
    )
    output_dir = directory or CACHE_DIR
    output = (
        output_dir
        / f"{hashlib.sha1((result.video_url + result.audio_url).encode('utf-8')).hexdigest()}.mp4"
    )
    return await _merge_av(video_path, audio_path, output)


async def download_images(result: VideoResult, *, directory: Path | None = None) -> list[Path]:
    """下载解析结果中的图片。"""
    if not result.image_urls:
        raise DownloadError("解析结果没有图片")
    tasks = [
        _download_image(url, headers=result.headers, directory=directory)
        for url in result.image_urls
    ]
    paths = [path for path in await asyncio.gather(*tasks) if path is not None]
    if not paths:
        raise DownloadError("图片下载失败")
    return paths


async def _download_image(
    url: str, *, headers: dict[str, str], directory: Path | None = None
) -> Path | None:
    """下载图片，失败时尝试常见无样式原图地址。"""
    for candidate in _url_candidates(url):
        try:
            return await download_file(
                candidate, suffix=".jpg", headers=headers, directory=directory
            )
        except Exception:
            continue
    return None


def _url_candidates(url: str) -> tuple[str, ...]:
    """生成媒体下载候选 URL。"""
    candidates = [url]
    if url.startswith("http://"):
        candidates.append("https://" + url[7:])
    base = url.split("!", 1)[0]
    if base != url:
        candidates.append(base)
        if base.startswith("http://"):
            candidates.append("https://" + base[7:])
    return tuple(dict.fromkeys(candidates))
