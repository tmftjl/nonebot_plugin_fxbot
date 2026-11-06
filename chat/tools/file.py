"""send_file：把模型生成的内容存成文件并发送到当前会话。"""

from __future__ import annotations

from pathlib import Path

from nonebot import logger

from .types import ToolError, ToolContext
from .runtime import ToolRuntime
from .registry import tool
from ...adapter import UnsupportedCapability, send_file_to_target, extract_message_target
from ...utils.paths import cache_dir

MAX_FILE_CHARS = 200_000


def _filename(value: str) -> str:
    """只接受纯文件名：不能带路径、不能是 . 或 ..，扩展名不限。"""
    name = str(value or "").strip()
    if (
        not name
        or len(name) > 100
        or name in {".", ".."}
        or name != Path(name).name
        or any(ord(char) < 32 for char in name)
    ):
        raise ToolError("文件名必须是不含路径的纯文件名，最长 100 字符。", code="invalid_file_name")
    return name


@tool(
    name="send_file",
    title="生成并发送文件",
    description=(
        "把你要给出的完整内容存成文件发到当前对话。name 只填文件名（带扩展名），"
        "内容全部放在 content 里；它不会读取服务器上的任何既有文件。"
        "对方明确要文件（例如「整理成 md 发我」）时才用，正文里不要再重复文件内容。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "文件名，带扩展名，例如 报告.md"},
            "content": {"type": "string", "description": "文件全文"},
        },
        "required": ["name", "content"],
    },
)
async def send_file_tool(context: ToolContext, runtime: ToolRuntime, name: str = "", content: str = "") -> dict:
    """写入固定目录后发送，失败信息直接交给模型。"""
    del context
    filename = _filename(name)
    text = content if isinstance(content, str) else str(content or "")
    if len(text) > MAX_FILE_CHARS:
        raise ToolError(f"文件内容超过 {MAX_FILE_CHARS} 字，拆成多个文件再发。", code="file_too_large")
    path = cache_dir("chat") / filename
    try:
        path.write_text(text, encoding="utf-8")
    except OSError:
        logger.opt(exception=True).warning("[Chat] 写入待发送文件失败")
        raise ToolError("写入文件失败。", code="file_write_failed") from None
    try:
        await send_file_to_target(
            runtime.require_bot(), extract_message_target(runtime.require_event()), path, filename
        )
    except UnsupportedCapability:
        raise ToolError("当前平台不支持发送文件。", code="file_unsupported") from None
    except Exception:
        logger.opt(exception=True).warning("[Chat] 发送文件失败")
        raise ToolError("发送文件失败，可能是平台拒绝上传该文件。", code="file_send_failed") from None
    return {"sent": True, "name": filename, "bytes": path.stat().st_size}
