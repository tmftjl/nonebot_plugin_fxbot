"""read_tool_result：按编号取回被预览或整理过的工具原始结果。"""

from __future__ import annotations

from typing import Any

from .store import StoredToolResult, tool_result_store
from .types import ToolError, ToolContext
from .runtime import ToolRuntime
from .registry import tool

READ_DEFAULT_MAX_CHARS = 2000
READ_MAX_CHARS_LIMIT = 2800
KEYWORD_CONTEXT_CHARS = 250
KEYWORD_MAX_MATCHES = 5


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _read_by_offset(stored: StoredToolResult, offset: int, max_chars: int) -> str:
    """按偏移顺序读取一段原文。"""
    total_chars = len(stored.content)
    start = min(max(offset, 0), total_chars)
    end = min(start + max_chars, total_chars)
    header = f"[{stored.result_id} · {stored.tool_name} 原始结果 · 共 {total_chars} 字 · 本段 {start}–{end}]"
    if start >= total_chars:
        return f"{header}\n（offset 已超出原文末尾，没有更多内容。）"
    remaining_chars = total_chars - end
    footer = (
        f"\n（后面还有 {remaining_chars} 字，用 offset={end} 继续读取。）"
        if remaining_chars > 0
        else "\n（已到原文末尾。）"
    )
    return f"{header}\n{stored.content[start:end]}{footer}"


def _read_by_keyword(stored: StoredToolResult, keyword: str) -> str:
    """返回原文中包含关键词的片段。"""
    content = stored.content
    lowered_content = content.lower()
    lowered_keyword = keyword.lower()
    snippets: list[str] = []
    total_matches = 0
    search_from = 0
    last_snippet_end = -1
    while True:
        match_index = lowered_content.find(lowered_keyword, search_from)
        if match_index < 0:
            break
        total_matches += 1
        search_from = match_index + len(lowered_keyword)
        if match_index < last_snippet_end or len(snippets) >= KEYWORD_MAX_MATCHES:
            continue
        start = max(match_index - KEYWORD_CONTEXT_CHARS, 0)
        end = min(match_index + len(keyword) + KEYWORD_CONTEXT_CHARS, len(content))
        last_snippet_end = end
        snippets.append(f"--- 位置 {start}–{end} ---\n{content[start:end]}")

    header = (
        f"[{stored.result_id} · {stored.tool_name} 原始结果 · 共 {len(content)} 字"
        f" · 关键词“{keyword}”命中 {total_matches} 处]"
    )
    if not snippets:
        return f"{header}\n（原文中没有这个关键词，可以换一个词，或不填 keyword 按 offset 顺序读取。）"
    footer = (
        f"\n（只显示了前 {len(snippets)} 个片段；可以用 offset 读取指定位置附近的原文。）"
        if total_matches > len(snippets)
        else ""
    )
    return f"{header}\n" + "\n".join(snippets) + footer


@tool(
    name="read_tool_result",
    title="读取工具原始结果",
    description=(
        "取回某次工具调用保存的原始结果。联网工具预览或超长结果的整理结论会附有编号（如 r3）；"
        "需要其中没有的细节时，用编号分段读取原文，或用 keyword 直接查找片段。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "result_id": {"type": "string", "description": "工具返回的原始结果编号，例如 r3。"},
            "keyword": {"type": "string", "description": "只返回包含该关键词的片段及其前后文。"},
            "offset": {"type": "integer", "minimum": 0, "description": "从原文第几个字开始读，默认 0。"},
            "max_chars": {
                "type": "integer",
                "minimum": 1,
                "maximum": READ_MAX_CHARS_LIMIT,
                "description": f"本次最多读取多少字，默认 {READ_DEFAULT_MAX_CHARS}。",
            },
        },
        "required": ["result_id"],
    },
)
async def read_tool_result_tool(
    context: ToolContext,
    runtime: ToolRuntime,
    result_id: str,
    keyword: str = "",
    offset: int = 0,
    max_chars: int = READ_DEFAULT_MAX_CHARS,
) -> str:
    """按编号读取暂存结果；编号不存在时提示重新调用原工具。"""
    del runtime
    value = str(result_id or "").strip()
    if not value:
        raise ToolError("read_tool_result 需要提供 result_id")
    stored = tool_result_store.get(context.session_id, value)
    if stored is None:
        raise ToolError(f"没有找到编号为 {value} 的原始结果：它可能已经过期，需要时请重新调用原来的工具。")
    keyword = str(keyword or "").strip()
    if keyword:
        return _read_by_keyword(stored, keyword)
    limit = min(max(_coerce_int(max_chars, READ_DEFAULT_MAX_CHARS), 1), READ_MAX_CHARS_LIMIT)
    return _read_by_offset(stored, _coerce_int(offset, 0), limit)


__all__ = ["read_tool_result_tool"]
