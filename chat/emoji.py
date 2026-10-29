"""表情包入库、挑选与发送；文件在 ai_chat/emoji。"""

from __future__ import annotations

import re
import json
import math
import base64
import random
import asyncio
import hashlib
from io import BytesIO
from typing import Any
from pathlib import Path
from datetime import datetime, timedelta

from PIL import Image, ImageDraw, ImageFont
from nonebot import logger
from rapidfuzz import fuzz

from .vision import review_emoji, describe_emoji
from ..config import get_manager as get_config_manager
from ..utils.paths import config_dir
from .memory.models import ChatEmoji, utc_now_naive
from .message_adapter import load_image_bytes, emoji_sources_for_event
from .memory.repository import chat_repository
from .providers.routing import routed_task, fallback_provider

MAX_EMOJI_BYTES = 5 * 1024 * 1024
EMOJI_COOLDOWN = timedelta(minutes=5)
_THUMB_SIZE = 320
_COLLAGE_TILE = 256
_COLLAGE_BADGE = 48
_MAX_REPLACE_CANDIDATES = 20  # 对齐 MaiBot emoji_manager.MAX_EMOJI_FOR_PROMPT
_ANALYZE_CONCURRENCY = 2
_MIN_SCORE = 45
_TOP_PICKS = 3

_analysis_tasks: dict[str, asyncio.Task[None]] = {}
_tenant_locks: dict[str, asyncio.Lock] = {}
_analysis_slots = asyncio.Semaphore(_ANALYZE_CONCURRENCY)


def emoji_config() -> dict[str, Any]:
    """读取表情配置；缺失或非法时按关闭处理。"""
    try:
        value = get_config_manager().get_system()["chat"].get("emoji") or {}
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def emoji_dir() -> Path:
    """表情文件目录。"""
    return config_dir("ai_chat") / "emoji"


def _extension(data: bytes) -> str:
    """按文件头识别图片后缀。"""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data.startswith(b"BM"):
        return ".bmp"
    raise ValueError("只支持 PNG/JPG/GIF/WEBP/BMP 图片")


def _emoji_path(tenant: str, digest: str, suffix: str) -> Path:
    """按租户隔离的表情文件路径。"""
    folder = emoji_dir() / hashlib.sha256(tenant.encode("utf-8")).hexdigest()[:16]
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{digest}{suffix}"


def _mime(data: bytes) -> str:
    """按文件头给出 data URL 的 MIME。"""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/bmp" if data.startswith(b"BM") else "image/jpeg"


def _max_collect_bytes() -> int | None:
    """采集表情的大小上限；0 表示不限。"""
    value = emoji_config().get("max_emoji_size_mb", 5)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        value = 5
    return int(value * 1024 * 1024) or None


def _write_file(tenant: str, digest: str, data: bytes) -> Path:
    """原子写入表情文件，返回路径。"""
    path = _emoji_path(tenant, digest, _extension(data))
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(data)
    temporary.replace(path)
    return path


def emoji_status(row: Any) -> str:
    """把三个状态位映射成控制台状态。"""
    if not getattr(row, "enabled", True):
        return "disabled"
    if getattr(row, "registered", False):
        return "registered"
    return "pending" if getattr(row, "vlm_processed", False) else "unidentified"


def _tenant_lock(tenant: str) -> asyncio.Lock:
    """同一租户的注册名额检查串行执行。"""
    return _tenant_locks.setdefault(tenant, asyncio.Lock())


async def collect_from_event(tenant: str, event: Any) -> None:
    """从消息里采集表情包；下载、去重后安排后台识别。"""
    if not emoji_config().get("steal_emoji", True):
        return
    limit = _max_collect_bytes()
    for source in emoji_sources_for_event(event)[:3]:
        try:
            data = await load_image_bytes(source, limit=limit)
        except Exception as exc:
            logger.debug(f"[ChatEmoji] 表情采集跳过: {exc}")
            continue
        if not data:
            continue
        digest = hashlib.sha256(data).hexdigest()
        try:
            if await chat_repository.get_emoji(tenant, digest):
                continue
            path = _write_file(tenant, digest, data)
            await chat_repository.save_collected_emoji(tenant, digest, str(path))
        except Exception:
            logger.opt(exception=True).debug("[ChatEmoji] 表情采集落盘失败")
            continue
        _schedule_analysis(tenant, digest, path)


def collect_in_background(tenant: str, event: Any) -> None:
    """异步采集表情，失败只记日志。"""

    async def run() -> None:
        try:
            await collect_from_event(tenant, event)
        except Exception:
            logger.opt(exception=True).debug("[ChatEmoji] 表情采集失败")

    asyncio.create_task(run())


def _schedule_analysis(tenant: str, digest: str, path: Path) -> None:
    """同一租户同一哈希只保留一个后台识别任务。"""
    key = f"{tenant}:{digest}"
    running = _analysis_tasks.get(key)
    if running is not None and not running.done():
        return
    task = asyncio.create_task(_analyze(tenant, digest, path))
    _analysis_tasks[key] = task

    def finish(done: asyncio.Task[None]) -> None:
        if _analysis_tasks.get(key) is done:
            _analysis_tasks.pop(key, None)

    task.add_done_callback(finish)


async def _analyze(tenant: str, digest: str, path: Path) -> None:
    """后台识别表情：描述、审核、按名额注册。"""
    cfg = emoji_config()
    async with _analysis_slots:
        try:
            data = await asyncio.to_thread(path.read_bytes)
        except OSError:
            return
        url = f"data:{_mime(data)};base64,{base64.b64encode(data).decode('ascii')}"
        try:
            async with fallback_provider("vision", tenant) as provider:
                description, tags = await describe_emoji(provider, url)
                passed = bool(description)
                if passed and cfg.get("content_filtration"):
                    passed = await review_emoji(provider, url)
        except Exception:
            logger.opt(exception=True).debug("[ChatEmoji] 表情识别失败，保留待审核")
            return
        if not description:
            return
        try:
            await chat_repository.set_emoji_analysis(tenant, digest, description=description, tags=tags, passed=False)
            if passed and await _ensure_slot(tenant, description):
                await chat_repository.set_emoji_registered(tenant, digest, True)
        except Exception:
            logger.opt(exception=True).warning("[ChatEmoji] 表情注册写库失败")


async def _ensure_slot(tenant: str, description: str) -> bool:
    """检查注册名额；满额时按配置替换旧表情。"""
    cfg = emoji_config()
    try:
        maximum = int(cfg.get("max_reg_num", 64))
    except (TypeError, ValueError):
        maximum = 64
    if maximum <= 0:
        return False
    async with _tenant_lock(tenant):
        counts = await chat_repository.count_emoji(tenant)
        if counts["registered"] < maximum:
            return True
        if not cfg.get("do_replace", True):
            return False
        return await _replace_by_llm(tenant, description, maximum)


async def _replace_by_llm(tenant: str, description: str, maximum: int) -> bool:
    """让文本模型挑一个旧表情取消注册；失败返回 False。"""
    rows = await chat_repository.list_emoji(tenant, status="registered")
    if not rows:
        return False
    picked = random.choices(
        rows,
        weights=[1 / (row.count + 1) for row in rows],
        k=min(_MAX_REPLACE_CANDIDATES, len(rows)),
    )
    unique = list({row.hash: row for row in picked}.values())
    lines = [
        f"编号: {index}\n描述: {row.description or '无描述'}\n使用次数: {row.count}"
        for index, row in enumerate(unique, 1)
    ]
    prompt = (
        f"机器人的可发送表情包数量已满({len(rows)}/{maximum})，"
        "需要决定是否取消注册一个旧表情包来为新表情包腾出名额。\n\n"
        f"新表情包信息：\n描述: {description or '无描述'}\n\n"
        "现有表情包列表：\n" + "\n".join(lines) + "\n\n"
        "请只回答：'不取消注册'或'取消注册编号X'（X为表情包编号）。"
    )
    try:
        async with fallback_provider(routed_task("emoji", "planner"), tenant) as provider:
            response = await provider.chat([{"role": "user", "content": prompt}], temperature=0.1, max_tokens=32)
    except Exception:
        logger.opt(exception=True).debug("[ChatEmoji] 替换决策失败")
        return False
    text = str(getattr(response, "content", "") or "")
    if "不取消注册" in text:
        return False
    match = re.search(r"取消注册编号\s*(\d+)", text)
    if not match:
        return False
    index = int(match.group(1)) - 1
    if not 0 <= index < len(unique):
        return False
    return await chat_repository.set_emoji_registered(tenant, unique[index].hash, False)


async def cleanup_emoji_cache(*, now: datetime | None = None) -> dict[str, int]:
    """清理未注册表情文件与无文件记录；已注册永不删除。"""
    settings = emoji_config().get("cache_cleanup")
    if not isinstance(settings, dict) or not settings.get("enabled", True):
        return {"files": 0, "records": 0}
    file_days = max(1, int(settings.get("emoji_file_retention_days", 30)))
    record_days = max(1, int(settings.get("no_file_record_retention_days", 30)))
    current = now or utc_now_naive()
    rows = await chat_repository.list_unregistered_emoji(
        older_than=current - timedelta(days=min(file_days, record_days))
    )
    files = records = 0
    for row in rows:
        age = (current - row.created_at).days
        target = Path(row.path)
        if target.exists() and age >= file_days:
            remove_emoji_files(row.path, row.hash)
            files += 1
        if not Path(row.path).exists() and age >= record_days:
            await chat_repository.delete_emoji_row(row.tenant, row.hash)
            records += 1
    return {"files": files, "records": records}


async def reconcile_emoji_cache() -> None:
    """补识别上次没处理完的表情，文件不在的跳过等清理。"""
    try:
        rows = await chat_repository.list_unanalyzed_emoji(limit=20)
    except Exception:
        logger.opt(exception=True).debug("[ChatEmoji] 表情补识别读取失败")
        return
    for row in rows:
        path = Path(row.path)
        if path.is_file():
            _schedule_analysis(row.tenant, row.hash, path)


async def emoji_reconcile_tick() -> None:
    """定时扫描待识别表情。"""
    try:
        await reconcile_emoji_cache()
    except Exception:
        logger.opt(exception=True).warning("[ChatEmoji] 表情补识别扫描失败")


async def emoji_cleanup_tick() -> None:
    """定时清理表情缓存，有变化才记日志。"""
    try:
        stats = await cleanup_emoji_cache()
    except Exception:
        logger.opt(exception=True).warning("[ChatEmoji] 表情缓存清理失败")
        return
    if stats["files"] or stats["records"]:
        logger.info(f"[ChatEmoji] 清理未注册表情：文件 {stats['files']}，记录 {stats['records']}")


async def save_emoji(tenant: str, data: bytes, *, description: str = "", tags: list[str] | None = None) -> ChatEmoji:
    """保存表情文件与索引；同一内容重复上传只刷新描述。"""
    if not data:
        raise ValueError("图片内容为空")
    if len(data) > MAX_EMOJI_BYTES:
        raise ValueError("图片不能超过 5 MB")
    digest = hashlib.sha256(data).hexdigest()
    path = _write_file(tenant, digest, data)
    return await chat_repository.save_emoji(tenant, digest, str(path), description=description, tags=tags or [])


def _tags(row: ChatEmoji) -> list[str]:
    """解析标签 JSON；坏数据按空处理。"""
    try:
        values = json.loads(row.tags or "[]")
    except (TypeError, ValueError):
        return []
    return [str(item) for item in values if str(item).strip()] if isinstance(values, list) else []


def _score(text: str, row: ChatEmoji) -> float:
    """文本与描述、标签的模糊匹配分。"""
    values = [row.description, *_tags(row)]
    return max((fuzz.partial_ratio(text, value) for value in values if value), default=0.0)


def _cooled(row: ChatEmoji, now: datetime) -> bool:
    """距离上次发送是否已过冷却。"""
    return row.last_used is None or now - row.last_used >= EMOJI_COOLDOWN


async def pick_emoji(
    tenant: str, text: str, *, now: datetime | None = None, rng: random.Random | None = None
) -> ChatEmoji | None:
    """按当前回复挑选一条未冷却且匹配的表情。"""
    config = emoji_config()
    if not str(text or "").strip():
        return None
    try:
        rows = await chat_repository.list_emoji(tenant, registered_only=True)
    except Exception:
        logger.opt(exception=True).debug("[ChatEmoji] 读取表情失败")
        return None
    current = now or utc_now_naive()
    scored = []
    for row in rows:
        score = _score(text, row)
        if score >= _MIN_SCORE and _cooled(row, current):
            scored.append((score, row))
    if not scored:
        return None
    scored.sort(key=lambda item: (item[0], item[1].count), reverse=True)
    return (rng or random.Random()).choice(scored[: min(_candidate_count(config), len(scored))])[1]


async def emoji_catalog(tenant: str, *, limit: int | None = None) -> list[ChatEmoji]:
    """规划器挑选用的候选表情：已注册、启用且文件仍在。"""
    try:
        rows = await chat_repository.list_emoji(tenant, registered_only=True)
    except Exception:
        logger.opt(exception=True).debug("[ChatEmoji] 读取候选表情失败")
        return []
    rows = [row for row in rows if Path(row.path).is_file()]
    count = limit if limit is not None else _candidate_count(emoji_config())
    return rows if len(rows) <= count else random.sample(rows, count)


def _collage_tile(path: str, index: int) -> Image.Image:
    """单格候选：图片居中铺在白底上，左上角写序号。"""
    tile = Image.new("RGB", (_COLLAGE_TILE, _COLLAGE_TILE), "white")
    try:
        with Image.open(path) as image:
            image.seek(0)
            image.thumbnail((_COLLAGE_TILE, _COLLAGE_TILE))
            rgba = image.convert("RGBA")
        tile.paste(rgba, ((_COLLAGE_TILE - rgba.width) // 2, (_COLLAGE_TILE - rgba.height) // 2), rgba)
    except Exception:
        logger.opt(exception=True).debug(f"[ChatEmoji] 拼图读取失败: {path}")
    draw = ImageDraw.Draw(tile)
    draw.rectangle((0, 0, _COLLAGE_BADGE, _COLLAGE_BADGE), fill="black")
    draw.text(
        (_COLLAGE_BADGE // 5, _COLLAGE_BADGE // 10),
        str(index),
        fill="white",
        font=ImageFont.load_default(size=32),
    )
    return tile


def emoji_collage(rows: list[ChatEmoji]) -> str | None:
    """把候选表情拼成带序号的网格图，返回 data URL；没有候选时返回 None。"""
    if not rows:
        return None
    columns = max(1, math.ceil(math.sqrt(len(rows))))
    canvas = Image.new("RGB", (columns * _COLLAGE_TILE, math.ceil(len(rows) / columns) * _COLLAGE_TILE), "white")
    for index, row in enumerate(rows, start=1):
        offset = (((index - 1) % columns) * _COLLAGE_TILE, ((index - 1) // columns) * _COLLAGE_TILE)
        canvas.paste(_collage_tile(row.path, index), offset)
    buffer = BytesIO()
    canvas.save(buffer, "PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


async def emoji_by_hash(tenant: str, digest: str) -> ChatEmoji | None:
    """按哈希取一条可发送的表情；未注册、已禁用或文件不在时返回 None。"""
    try:
        row = await chat_repository.get_emoji(tenant, digest)
    except Exception:
        logger.opt(exception=True).debug("[ChatEmoji] 读取指定表情失败")
        return None
    if row is None or not row.registered or not row.enabled or not Path(row.path).is_file():
        return None
    return row


def _candidate_count(config: dict[str, Any]) -> int:
    """单次发送的候选池大小。"""
    value = config.get("emoji_send_num", _TOP_PICKS)
    if isinstance(value, bool) or not isinstance(value, int):
        return _TOP_PICKS
    return max(1, min(64, value))


async def mark_used(row: ChatEmoji, *, now: datetime | None = None) -> None:
    """记录一次发送，进入冷却。"""
    try:
        await chat_repository.mark_emoji_used(row.tenant, row.hash, now=now)
    except Exception:
        logger.opt(exception=True).debug("[ChatEmoji] 记录发送失败")


def emoji_thumbnail(digest: str, source: str) -> Path:
    """按需生成表情缩略图；原图更新或缓存缺失时重建。"""
    source_path = Path(source)
    target = source_path.with_name(f"{digest}.thumb.jpg")
    if target.is_file() and target.stat().st_mtime_ns >= source_path.stat().st_mtime_ns:
        return target
    with Image.open(source_path) as image:
        image.seek(0)
        image.thumbnail((_THUMB_SIZE, _THUMB_SIZE))
        if image.mode in {"RGBA", "LA", "P"}:
            alpha = image.convert("RGBA")
            canvas = Image.new("RGB", alpha.size, "white")
            canvas.paste(alpha, mask=alpha.split()[-1])
            image = canvas
        elif image.mode != "RGB":
            image = image.convert("RGB")
        temporary = target.with_name(f"{target.name}.{random.getrandbits(32):08x}.tmp")
        try:
            image.save(temporary, "JPEG", quality=85)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    return target


def _remove_inside(target: Path) -> None:
    """只删除表情目录内的文件，避免误删外部路径。"""
    try:
        target.resolve().relative_to(emoji_dir().resolve())
    except (OSError, ValueError):
        return
    try:
        target.unlink(missing_ok=True)
    except OSError:
        logger.opt(exception=True).warning("[ChatEmoji] 删除表情文件失败")


def remove_emoji_files(path: str, digest: str) -> None:
    """删除表情原图与缩略图缓存。"""
    _remove_inside(Path(path))
    _remove_inside(Path(path).with_name(f"{digest}.thumb.jpg"))
