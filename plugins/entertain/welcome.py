"""群欢迎语功能。"""

from __future__ import annotations

import base64
import json
import re
import shutil
import uuid
from pathlib import Path
from typing import Any

from nonebot import on_notice
from nonebot.adapters import Bot, Event
from nonebot.matcher import Matcher

from ...adapter import build_message, build_message_segment
from ...permission import PermLevel, PermScene
from ...plugin import Plugin
from ...utils.http import get_shared_async_client
from ...utils.paths import data_dir

P = Plugin(
    "entertain",
    display_name="娱乐",
    enabled=True,
    level=PermLevel.LOW,
    scene=PermScene.ALL,
)

WELCOME_DIR = data_dir("entertain")
WELCOME_FILE = WELCOME_DIR / "welcome.json"
WELCOME_IMG_ROOT = data_dir("welcome")

_PH_PATTERN = re.compile(r"\[\[WELCOME_IMG:([^\]]+)\]\]")
_SET_WELCOME_PREFIX_RE = re.compile(r"^\s*[#＃]设置欢迎\s*")


def _load_store() -> dict[str, dict[str, Any]]:
    """读取欢迎语存储。"""
    try:
        if WELCOME_FILE.exists():
            data = json.loads(WELCOME_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        return {}
    return {}


def _save_store(data: dict[str, dict[str, Any]]) -> None:
    """保存欢迎语存储。"""
    WELCOME_FILE.parent.mkdir(parents=True, exist_ok=True)
    WELCOME_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _group_key(event: Event) -> str | None:
    """提取群 ID。"""
    value = getattr(event, "group_id", None)
    if value is None and hasattr(event, "get_group_id"):
        try:
            value = event.get_group_id()
        except Exception:
            value = None
    text = str(value or "").strip()
    return text or None


def _group_img_dir(group_key: str) -> Path:
    """返回群欢迎图片目录。"""
    return (WELCOME_IMG_ROOT / str(group_key)).resolve()


def _reset_group_img_dir(group_key: str) -> Path:
    """清空并重建群欢迎图片目录。"""
    directory = _group_img_dir(group_key)
    if directory.exists():
        shutil.rmtree(directory, ignore_errors=True)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _guess_ext(data: bytes) -> str:
    """根据文件头猜测图片扩展名。"""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"GIF8"):
        return ".gif"
    if data[:4] == b"RIFF" and b"WEBP" in data[:16]:
        return ".webp"
    return ".bin"


async def _image_bytes(bot: Bot, segment: Any) -> bytes | None:
    """读取图片消息段内容。"""
    data = getattr(segment, "data", {}) or {}
    file_value = str(data.get("file") or "")
    if file_value.startswith("base64://"):
        try:
            return base64.b64decode(file_value.split("base64://", 1)[1])
        except Exception:
            return None
    url = data.get("url")
    if isinstance(url, str) and url.startswith(("http://", "https://")):
        try:
            client = await get_shared_async_client()
            response = await client.get(url)
            response.raise_for_status()
            return response.content
        except Exception:
            return None
    if file_value and hasattr(bot, "call_api"):
        try:
            info = await bot.call_api("get_image", file=file_value)
            path = (info or {}).get("file")
            if path:
                return Path(path).read_bytes()
        except Exception:
            return None
    return None


def _strip_command_prefix(message: Any) -> list[Any]:
    """剔除 #设置欢迎 前缀并保留图片段。"""
    segments: list[Any] = []
    accumulated = ""
    stripped = False
    for segment in list(message or []):
        seg_type = getattr(segment, "type", None)
        if not stripped and seg_type == "text":
            text = str((getattr(segment, "data", {}) or {}).get("text") or "")
            accumulated += text
            after = _SET_WELCOME_PREFIX_RE.sub("", accumulated, count=1)
            if after != accumulated:
                stripped = True
                if after.lstrip():
                    segments.append(("text", after.lstrip()))
                accumulated = ""
            continue
        if not stripped:
            if accumulated.strip():
                segments.append(("text", accumulated.lstrip()))
            stripped = True
            accumulated = ""
        segments.append(segment)
    if not stripped and accumulated.strip():
        segments.append(("text", accumulated.lstrip()))
    return segments


def _has_valid_content(segments: list[Any]) -> bool:
    """判断欢迎语是否包含有效文本或图片。"""
    for segment in segments:
        if (
            isinstance(segment, tuple)
            and segment[0] == "text"
            and str(segment[1]).strip()
        ):
            return True
        if getattr(segment, "type", None) == "image":
            return True
        if getattr(segment, "type", None) == "text":
            if str((getattr(segment, "data", {}) or {}).get("text") or "").strip():
                return True
    return False


async def _serialize_text_and_images(
    bot: Bot, segments: list[Any], group_key: str
) -> tuple[str, dict[str, int]]:
    """序列化文本和图片到欢迎语存储。"""
    _reset_group_img_dir(group_key)
    directory = _group_img_dir(group_key)
    parts: list[str] = []
    meta = {"images_saved": 0, "images_failed": 0, "text_len": 0, "segments_ignored": 0}
    for segment in segments:
        if isinstance(segment, tuple) and segment[0] == "text":
            text = str(segment[1])
            parts.append(text)
            meta["text_len"] += len(text)
            continue
        seg_type = getattr(segment, "type", None)
        data = getattr(segment, "data", {}) or {}
        if seg_type == "image":
            content = await _image_bytes(bot, segment)
            if not content:
                meta["images_failed"] += 1
                continue
            filename = uuid.uuid4().hex + _guess_ext(content)
            (directory / filename).write_bytes(content)
            parts.append(f"[[WELCOME_IMG:{filename}]]")
            meta["images_saved"] += 1
        elif seg_type == "text":
            text = str(data.get("text") or "")
            parts.append(text)
            meta["text_len"] += len(text)
        else:
            meta["segments_ignored"] += 1
    return "".join(parts), meta


def _render_welcome_content(bot: Bot, group_key: str | None, text: str) -> list[Any]:
    """渲染欢迎语消息段。"""
    parts: list[Any] = []
    pos = 0
    for match in _PH_PATTERN.finditer(text):
        start, end = match.span()
        if start > pos:
            parts.append(build_message_segment(bot, "text", text[pos:start]))
        filename = match.group(1)
        base = _group_img_dir(group_key) if group_key else WELCOME_IMG_ROOT
        path = (base / filename).resolve()
        try:
            parts.append(build_message_segment(bot, "image", path.read_bytes()))
        except Exception:
            parts.append(build_message_segment(bot, "text", match.group(0)))
        pos = end
    if pos < len(text):
        parts.append(build_message_segment(bot, "text", text[pos:]))
    return parts


set_welcome_cmd = P.on_regex(
    r"^[#＃]设置欢迎(?:\s*(.+))?",
    name="set",
    display_name="设置欢迎",
    priority=5,
    block=True,
    level=PermLevel.ADMIN,
    scene=PermScene.GROUP,
)

show_welcome_cmd = P.on_regex(
    r"^[#＃]?查看欢迎",
    name="show",
    display_name="查看欢迎",
    priority=5,
    block=True,
    level=PermLevel.LOW,
    scene=PermScene.GROUP,
)

enable_welcome_cmd = P.on_regex(
    r"^[#＃]?开启欢迎",
    name="enable",
    display_name="开启欢迎",
    priority=5,
    block=True,
    level=PermLevel.ADMIN,
    scene=PermScene.GROUP,
)

disable_welcome_cmd = P.on_regex(
    r"^[#＃]?关闭欢迎",
    name="disable",
    display_name="关闭欢迎",
    priority=5,
    block=True,
    level=PermLevel.ADMIN,
    scene=PermScene.GROUP,
)


@set_welcome_cmd.handle()
async def _handle_set_welcome(matcher: Matcher, bot: Bot, event: Event) -> None:
    """设置欢迎语。"""
    group_key = _group_key(event)
    if not group_key:
        await matcher.finish("请在群聊中使用该命令")
    try:
        message = event.get_message()
    except Exception:
        message = getattr(event, "message", [])
    payload = _strip_command_prefix(message)
    if not _has_valid_content(payload):
        await matcher.finish("请提供欢迎内容，仅支持文本与图片")
    serialized, meta = await _serialize_text_and_images(bot, payload, group_key)
    if not serialized:
        await matcher.finish("欢迎内容无效：仅支持文本与图片")
    store = _load_store()
    store[group_key] = {"enabled": True, "content": serialized}
    _save_store(store)
    await matcher.finish(
        f"已更新欢迎：图片 {meta['images_saved']} 张，文本 {meta['text_len']} 字"
    )


@show_welcome_cmd.handle()
async def _handle_show_welcome(matcher: Matcher, bot: Bot, event: Event) -> None:
    """查看欢迎语。"""
    group_key = _group_key(event)
    if not group_key:
        await matcher.finish("请在群聊中使用该命令")
    record = _load_store().get(group_key)
    if not record:
        await matcher.finish("当前未设置欢迎语")
    status = "开启" if record.get("enabled", True) else "关闭"
    message = build_message(
        bot,
        build_message_segment(bot, "text", f"当前欢迎已{status}\n"),
        *_render_welcome_content(bot, group_key, str(record.get("content", ""))),
    )
    await matcher.finish(message)


@enable_welcome_cmd.handle()
async def _handle_enable_welcome(matcher: Matcher, event: Event) -> None:
    """开启欢迎语。"""
    group_key = _group_key(event)
    if not group_key:
        await matcher.finish("请在群聊中使用该命令")
    store = _load_store()
    record = store.get(group_key) or {}
    record["enabled"] = True
    store[group_key] = record
    _save_store(store)
    await matcher.finish("已开启本群欢迎")


@disable_welcome_cmd.handle()
async def _handle_disable_welcome(matcher: Matcher, event: Event) -> None:
    """关闭欢迎语。"""
    group_key = _group_key(event)
    if not group_key:
        await matcher.finish("请在群聊中使用该命令")
    store = _load_store()
    record = store.get(group_key) or {}
    record["enabled"] = False
    store[group_key] = record
    _save_store(store)
    await matcher.finish("已关闭本群欢迎")


welcome_notice = on_notice(priority=12, block=False, permission=P.permission())


@welcome_notice.handle()
async def _handle_group_increase(bot: Bot, event: Event) -> None:
    """新成员入群时发送欢迎语。"""
    notice_type = str(getattr(event, "notice_type", "") or "")
    sub_type = str(getattr(event, "sub_type", "") or "")
    if (
        notice_type != "group_increase"
        and "increase" not in type(event).__name__.lower()
    ):
        return
    if sub_type == "invite" and getattr(event, "user_id", None) is None:
        return
    user_id = str(getattr(event, "user_id", "") or "")
    if user_id == str(getattr(bot, "self_id", "")):
        return
    group_key = _group_key(event)
    if not group_key:
        return
    record = _load_store().get(group_key)
    if not record or not record.get("enabled", True):
        return
    content = str(record.get("content", ""))
    if not content:
        return
    await bot.send(
        event,
        build_message(
            bot,
            build_message_segment(bot, "at", user_id) if user_id else None,
            build_message_segment(bot, "text", " "),
            *_render_welcome_content(bot, group_key, content),
        ),
    )
