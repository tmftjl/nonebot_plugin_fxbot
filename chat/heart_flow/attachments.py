"""适配 MaiBot reply 附图及 tool_result 媒体索引。"""

from __future__ import annotations

import json
import base64
import asyncio
import binascii
from html import escape
from typing import Any
from pathlib import Path

from ..message_adapter import load_image_bytes
from ..memory.repository import chat_repository

MEDIA_TYPES = {"image", "audio", "resource_link", "resource", "binary"}


def _decode_media(data: str | bytes) -> bytes:
    """按上游规则读取工具的内联字节或 base64 数据。"""
    if isinstance(data, bytes):
        return data
    value = data.strip()
    if value.startswith("data:") and "," in value:
        value = value.split(",", 1)[1].strip()
    try:
        return base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        try:
            return base64.b64decode(value + "=" * (-len(value) % 4))
        except (ValueError, binascii.Error):
            return b""


def _media_attributes(identifier: str, item: dict[str, Any]) -> str:
    """只展示上游定义的简单媒体属性并转义 XML 字符。"""
    attributes = {
        "msg_id": identifier,
        "type": item["content_type"],
        "mime": item.get("mime_type"),
        "name": item.get("name"),
    }
    metadata = item.get("metadata") or {}
    for key in ("context_key", "source_url"):
        value = metadata.get(key)
        if not isinstance(value, (dict, list, tuple, set)):
            attributes[key] = value
    return " ".join(f'{key}="{escape(str(value), quote=True)}"' for key, value in attributes.items() if value)


def _history_text(item: dict[str, Any]) -> str:
    """保留上游媒体内容项的可读摘要，不将二进制正文写入工具文本。"""
    kind = item.get("content_type")
    if kind == "text" or (kind == "resource" and item.get("text")):
        return str(item.get("text") or "").strip()
    label = {"image": "图片内容", "audio": "音频内容", "binary": "二进制内容"}.get(kind)
    if label:
        return f"[{label} {item.get('mime_type') or 'unknown'}]"
    if kind in {"resource_link", "resource"}:
        label = "资源链接" if kind == "resource_link" else "嵌入资源"
        return f"[{label}] {item.get('name') or item.get('uri') or label}"
    return ""


def tool_media(result: dict[str, Any], call_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """按上游投影五类工具媒体，只有有效的内联图片进入视觉内容。"""
    value = result.get("result")
    if not result.get("ok") or not isinstance(value, dict):
        return result, []
    items = value.get("content_items")
    if not isinstance(items, list):
        return result, []
    additions = []
    indexes = []
    media_lines = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict) or item.get("content_type") not in MEDIA_TYPES:
            continue
        if not any(item.get(key) for key in ("data", "uri", "name", "description", "mime_type")):
            continue
        identifier = f"tool_result:{call_id}:{index}"
        attributes = _media_attributes(identifier, item)
        indexes.append(identifier)
        media_lines.append(f"  <media {attributes} />")
        content = [{"type": "text", "text": f"<tool_result_media {attributes} />"}]
        mapping = {}
        mime = str(item.get("mime_type") or "")
        if item["content_type"] == "image" or (item["content_type"] == "binary" and mime.startswith("image/")):
            raw = item.get("data") or ""
            if not raw and str(item.get("uri") or "").startswith("data:"):
                raw = item["uri"]
            data = _decode_media(raw)
            if data:
                source = f"data:{mime or 'image/png'};base64,{base64.b64encode(data).decode('ascii')}"
                content.append({"type": "image_url", "image_url": {"url": source}})
                mapping[identifier] = source
        additions.append({"role": "user", "content": content, "_tool_media": mapping, "_planner_internal": True})
    if not additions:
        return result, []
    projected = {key: value for key, value in value.items() if key != "content_items"}
    text = str(projected.get("content") or "")
    text_items = [_history_text(item) for item in items if isinstance(item, dict)] if not text.strip() else []
    projected["content"] = "\n".join(
        [part for part in [text, *text_items] if part]
        + ["<tool_result_media_list>", *media_lines, "</tool_result_media_list>"]
    )
    projected["media_indexes"] = indexes
    return {**result, "result": projected}, additions


async def resolve_pictures(service: Any, request: Any, arguments: Any, trace: list[dict[str, Any]]) -> list[bytes]:
    """仅从当前会话的真实消息或工具媒体取图，不接受模型指定文件路径。"""
    if arguments is None or arguments == "":
        return []
    if not isinstance(arguments, list) or not all(isinstance(entry, dict) for entry in arguments):
        raise ValueError("attach_pic 必须是图片引用对象数组")
    entries = arguments
    pictures = []
    history = [*service.session_store.get(request.session_id), *trace]
    for entry in entries:
        identifier = str(entry.get("media_index") or entry.get("msg_id") or "")
        index = entry.get("index", 0)
        if type(index) is not int or index < 0 or not identifier:
            raise ValueError("图片引用无效")
        source = None
        if identifier.startswith("tool_result:"):
            if index != 0:
                raise ValueError("工具图片引用无效")
            source = next(
                (item["_tool_media"][identifier] for item in history if identifier in item.get("_tool_media", {})), None
            )
        else:
            sources = []
            if identifier == (request.message_id or request.dedupe_key):
                sources = request.metadata.get("raw_images") or request.images
            if not sources:
                sources = next(
                    (item.get("_image_sources", []) for item in history if identifier in item.get("_source_ids", [])),
                    [],
                )
            if index < len(sources):
                source = sources[index]
            if source is None:
                row = await chat_repository.message_by_identity(request.session_id, identifier)
                items = json.loads(row.payload or "{}").get("image_memory", []) if row is not None else []
                if index < len(items) and items[index].get("path"):
                    source = await asyncio.to_thread(Path(items[index]["path"]).read_bytes)
        if source is None:
            raise ValueError("没有找到当前会话内的指定图片")
        if isinstance(source, str) and source.startswith("data:image/"):
            source = "base64://" + source.split(",", 1)[1]
        pictures.append(await load_image_bytes(source))
    return pictures
