"""表情包入库、挑选与发送；文件在 ai_chat/emoji。"""

from __future__ import annotations

import json
import random
import hashlib
from typing import Any
from pathlib import Path
from datetime import datetime, timedelta

from nonebot import logger
from rapidfuzz import fuzz

from ..config import get_manager as get_config_manager
from ..utils.paths import config_dir
from .memory.models import ChatEmoji, utc_now_naive
from .memory.repository import chat_repository

MAX_EMOJI_BYTES = 5 * 1024 * 1024
EMOJI_COOLDOWN = timedelta(minutes=5)
_MIN_SCORE = 45
_TOP_PICKS = 3


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


async def save_emoji(
    tenant: str, data: bytes, *, description: str = "", tags: list[str] | None = None
) -> ChatEmoji:
    """保存表情文件与索引；同一内容重复上传只刷新描述。"""
    if not data:
        raise ValueError("图片内容为空")
    if len(data) > MAX_EMOJI_BYTES:
        raise ValueError("图片不能超过 5 MB")
    digest = hashlib.sha256(data).hexdigest()
    path = _emoji_path(tenant, digest, _extension(data))
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(data)
    temporary.replace(path)
    return await chat_repository.save_emoji(
        tenant, digest, str(path), description=description, tags=tags or []
    )


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
    if not config.get("enabled") or not str(text or "").strip():
        return None
    try:
        rows = await chat_repository.list_emoji(tenant, enabled_only=True)
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
    return (rng or random.Random()).choice(scored[: min(_TOP_PICKS, len(scored))])[1]


async def mark_used(row: ChatEmoji, *, now: datetime | None = None) -> None:
    """记录一次发送，进入冷却。"""
    try:
        await chat_repository.mark_emoji_used(row.tenant, row.hash, now=now)
    except Exception:
        logger.opt(exception=True).debug("[ChatEmoji] 记录发送失败")


def remove_file(path: str) -> None:
    """删除表情文件；只处理表情目录内的路径。"""
    target = Path(path)
    try:
        target.resolve().relative_to(emoji_dir().resolve())
    except (OSError, ValueError):
        return
    try:
        target.unlink(missing_ok=True)
    except OSError:
        logger.opt(exception=True).warning("[ChatEmoji] 删除表情文件失败")
