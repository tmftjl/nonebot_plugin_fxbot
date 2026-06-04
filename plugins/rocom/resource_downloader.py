"""洛克王国运行时资源下载。"""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from urllib.parse import unquote, urljoin

from nonebot import get_driver, logger

from ...utils.http import get_shared_async_client
from ...utils.paths import data_dir
from .config import cfg_resources

RESOURCE_DIR = data_dir("rocom") / "resources"

DEFAULT_URLS = [
    ("[CNJS]", "http://cn-js-nj-1.lcf.icu:13214"),
    ("[TW]", "http://tw-taipei-1.lcf.icu:20532"),
    ("[SG]", "http://sg-1.lcf.icu:12588"),
    ("[US]", "http://us-lax-2.lcf.icu:12588"),
    ("[Azure SG]", "https://sg-2.qxqx.cf"),
    ("[Oracle KR]", "https://kr.qxqx.cf"),
    ("[Oracle JP]", "https://jp.qxqx.cf"),
    ("[MiniGG]", "http://file.minigg.cn/sayu-bot"),
    ("[Lulu]", "http://lulush.microgg.cn"),
    ("[TakeyaYuki]", "https://gscore.focalors.com"),
    ("[Elysia]", "https://silverwing.elysia.beauty"),
]

ENDPOINTS = {
    "resource/rocomicon": RESOURCE_DIR / "rocomicon",
    "resource/skillicon": RESOURCE_DIR / "skillicon",
    "resource/characteristicicon": RESOURCE_DIR / "characteristicicon",
    "resource/headicon": RESOURCE_DIR / "headicon",
}

_download_lock = asyncio.Lock()


def resources_ready() -> bool:
    """检查运行时图标资源是否已存在。"""
    for target in ENDPOINTS.values():
        if not target.is_dir():
            return False
        try:
            next(target.iterdir())
        except StopIteration:
            return False
    return True


async def ensure_rocom_resources(force: bool = False) -> None:
    """检查并下载 RocomUID 运行时资源。"""
    cfg = cfg_resources()
    if not bool(cfg.get("enabled", True)):
        return
    async with _download_lock:
        if resources_ready() and not force:
            return

        client = await get_shared_async_client()
        tag, base_url = await _choose_base_url(str(cfg.get("base_url") or "").strip())
        logger.info(f"[rocom] 使用资源站 {tag} {base_url} 下载运行时资源")
        total = 0
        for endpoint, target in ENDPOINTS.items():
            changed = await _download_endpoint(client, base_url, endpoint, target, int(cfg.get("concurrency") or 12))
            total += changed
            logger.info(f"[rocom] 资源 {endpoint} 检查完成，更新 {changed} 个文件")
        logger.info(f"[rocom] 运行时资源检查完成，更新 {total} 个文件")


async def _choose_base_url(configured: str) -> tuple[str, str]:
    """选择可用资源站。"""
    client = await get_shared_async_client()
    candidates = [("[Config]", configured)] if configured else DEFAULT_URLS
    tasks = [asyncio.create_task(_probe(client, tag, url)) for tag, url in candidates if url]
    try:
        for task in asyncio.as_completed(tasks):
            result = await task
            if result is not None:
                return result
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
    if configured:
        raise RuntimeError(f"配置资源站不可用：{configured}")
    raise RuntimeError("没有找到可用的 GsCore 资源站")


async def _probe(client, tag: str, base_url: str) -> tuple[str, str] | None:
    try:
        response = await client.get(base_url, timeout=8.0, follow_redirects=True)
        if response.status_code == 200 and "Index of /" in response.text:
            return tag, base_url.rstrip("/")
    except Exception:
        return None
    return None


async def _download_endpoint(client, base_url: str, endpoint: str, target: Path, concurrency: int) -> int:
    """递归下载目录索引中的文件。"""
    url = f"{base_url.rstrip('/')}/RocomUID/{endpoint.strip('/')}/"
    target.mkdir(parents=True, exist_ok=True)
    response = await client.get(url, timeout=60.0, follow_redirects=True)
    response.raise_for_status()
    hrefs = _parse_links(response.text)
    changed = 0
    semaphore = asyncio.Semaphore(max(1, min(concurrency, 32)))
    tasks = []
    for href in hrefs:
        if href == "../":
            continue
        file_url = urljoin(url, href)
        name = unquote(file_url.rstrip("/").split("/")[-1])
        if href.endswith("/"):
            changed += await _download_endpoint(client, base_url, f"{endpoint.rstrip('/')}/{href.strip('/')}", target / name, concurrency)
        else:
            tasks.append(_download_file(client, file_url, target / name, semaphore))
    if tasks:
        changed += sum(await asyncio.gather(*tasks))
    return changed


def _parse_links(html: str) -> list[str]:
    """解析资源站目录页链接。"""
    return [item for item in re.findall(r"""<a\s+href=["']([^"']+)["']""", html, flags=re.I) if item and not item.startswith("?")]


async def _download_file(client, url: str, path: Path, semaphore: asyncio.Semaphore) -> int:
    async with semaphore:
        response = await client.get(url, timeout=120.0, follow_redirects=True)
        response.raise_for_status()
        content = response.content
        if path.exists() and os.stat(path).st_size == len(content):
            return 0
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return 1


async def _startup_download() -> None:
    """后台启动资源下载。"""
    try:
        await ensure_rocom_resources()
    except Exception:
        logger.opt(exception=True).warning("[rocom] 运行时资源下载失败")


@get_driver().on_startup
async def _on_startup() -> None:
    """启动时后台检查资源。"""
    asyncio.create_task(_startup_download())
