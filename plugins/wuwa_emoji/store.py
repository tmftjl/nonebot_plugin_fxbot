"""鸣潮角色列表本地缓存。"""

from __future__ import annotations

import os
import json
import time
import asyncio

from nonebot import logger, get_driver

from .client import WuwaEmojiError, fetch_characters
from .config import cfg_character_cache_ttl
from ...utils.paths import cache_dir

CACHE_PATH = cache_dir("wuwa_emoji") / "characters.json"

_lock = asyncio.Lock()
_memory: tuple[float, list[dict[str, str]]] | None = None
_refresh_task: asyncio.Task[None] | None = None
_startup_hook_registered = False


def _read_disk() -> tuple[float, list[dict[str, str]]] | None:
    """读取磁盘缓存，缺失或损坏时返回 None。"""
    if not CACHE_PATH.exists():
        return None
    try:
        data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        fetched_at = float(data["fetched_at"])
        items = data["items"]
    except Exception:
        logger.opt(exception=True).warning("[wuwa_emoji] 角色缓存读取失败，将重新拉取")
        return None
    if not isinstance(items, list):
        return None
    return fetched_at, [item for item in items if isinstance(item, dict)]


def _write_disk(fetched_at: float, items: list[dict[str, str]]) -> None:
    """原子写入磁盘缓存，避免留下半截文件。"""
    tmp_path = CACHE_PATH.with_name(f"{CACHE_PATH.name}.tmp")
    try:
        payload = {"fetched_at": fetched_at, "items": items}
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp_path, CACHE_PATH)
    except Exception:
        logger.opt(exception=True).warning("[wuwa_emoji] 角色缓存写入失败")
        tmp_path.unlink(missing_ok=True)


async def refresh_characters() -> list[dict[str, str]]:
    """强制重新拉取角色列表并覆盖缓存，失败时抛出异常。"""
    global _memory
    async with _lock:
        items = await fetch_characters()
        _memory = (time.time(), items)
        _write_disk(_memory[0], items)
        return items


async def _background_refresh() -> None:
    """后台刷新角色列表，失败只记录日志。"""
    try:
        await refresh_characters()
    except WuwaEmojiError:
        logger.opt(exception=True).warning("[wuwa_emoji] 后台刷新角色列表失败")


def _schedule_refresh() -> None:
    """安排一次后台刷新，已有任务在跑时跳过。"""
    global _refresh_task
    if _refresh_task is not None and not _refresh_task.done():
        return
    try:
        _refresh_task = asyncio.create_task(_background_refresh())
    except RuntimeError:
        # 没有运行中的事件循环时放弃刷新，下次调用会重新安排
        logger.opt(exception=True).debug("[wuwa_emoji] 无法安排角色列表后台刷新")


def get_characters() -> list[dict[str, str]]:
    """读取角色列表。

    命令匹配发生在事件处理的并发关键路径上，因此这里只读缓存绝不联网：
    缓存过期时仅安排后台刷新，本次仍返回旧数据，避免把接口延迟传导给
    同优先级的其他 matcher 以及后续的 AI 兜底路由。
    """
    global _memory
    cached = _memory or _read_disk()
    if cached is None:
        _schedule_refresh()
        return []

    _memory = cached
    fetched_at, items = cached
    if time.time() - fetched_at >= cfg_character_cache_ttl():
        _schedule_refresh()
    return items


def match_character(items: list[dict[str, str]], name: str) -> dict[str, str] | None:
    """按角色名或 slug 匹配角色，大小写无关。"""
    target = name.strip().casefold()
    if not target:
        return None
    for item in items:
        if str(item.get("name") or "").casefold() == target:
            return item
    for item in items:
        if str(item.get("slug") or "").casefold() == target:
            return item
    return None


async def _startup_warmup() -> None:
    """启动时预热角色列表，不阻塞启动流程。"""
    if get_characters():
        return
    _schedule_refresh()


async def _shutdown_refresh() -> None:
    """关闭插件后台刷新任务。"""
    global _refresh_task
    task = _refresh_task
    _refresh_task = None
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


def setup_wuwa_emoji_tasks() -> None:
    """注册角色列表预热和关闭钩子。"""
    global _startup_hook_registered
    if not _startup_hook_registered:
        driver = get_driver()
        driver.on_startup(_startup_warmup)
        driver.on_shutdown(_shutdown_refresh)
        _startup_hook_registered = True
