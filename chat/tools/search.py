"""tool_search：按名称或关键词查找延迟工具。"""

from __future__ import annotations

from .types import ToolSpec, ToolError, ToolContext
from .runtime import ToolRuntime
from .executor import tool_allowed
from .registry import tool, default_registry
from .discovery import discover

TOOL_SEARCH_NAME = "tool_search"
_DEFAULT_LIMIT = 5


def _terms(query: str) -> list[str]:
    """按空白切分关键词，兼容中英文逗号。"""
    return [part for part in query.lower().replace("，", " ").replace(",", " ").split() if part]


def _score(spec: ToolSpec, terms: list[str]) -> int:
    """名称命中权重高于标题与描述。"""
    name = spec.name.lower()
    title = spec.title.lower()
    return sum((2 if term in name else 0) + (1 if term in title else 0) for term in terms)


def _hits(spec: ToolSpec, terms: list[str]) -> bool:
    return any(term in f"{spec.name} {spec.title} {spec.description}".lower() for term in terms)


@tool(
    name=TOOL_SEARCH_NAME,
    title="工具检索",
    description="按名称或关键词检索尚未直接提供的工具；找到后可在后续轮次调用。",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "工具名、前缀或功能关键词。"},
            "limit": {"type": "integer", "minimum": 1, "description": "最多返回多少个工具，默认 5。"},
        },
        "required": ["query"],
    },
)
async def tool_search_tool(
    context: ToolContext,
    runtime: ToolRuntime,
    query: str = "",
    limit: int = _DEFAULT_LIMIT,
) -> str:
    """在延迟工具里检索并记录发现。"""
    query = str(query or "").strip()
    if not query:
        raise ToolError("query 不能为空")
    try:
        count = max(1, int(limit or _DEFAULT_LIMIT))
    except (TypeError, ValueError):
        count = _DEFAULT_LIMIT
    raw_allowed = context.metadata.get("allowed_tools")
    allowed = None if raw_allowed is None else set(raw_allowed)
    terms = _terms(query)
    matched: list[ToolSpec] = []
    for spec in default_registry.list():
        if spec.name == TOOL_SEARCH_NAME or not spec.deferred:
            continue
        if allowed is not None and spec.name not in allowed:
            continue
        if not _hits(spec, terms) or not await tool_allowed(spec, runtime, session_id=context.session_id):
            continue
        matched.append(spec)
    matched.sort(key=lambda spec: _score(spec, terms), reverse=True)
    found = matched[:count]
    if not found:
        return "没有找到匹配的工具，可以换一个更完整的名称或关键词。"
    fresh = set(discover(context.session_id, (spec.name for spec in found)))
    lines = ["已找到以下工具，可在后续轮次调用："]
    for spec in found:
        label = spec.title or spec.description
        mark = "（本次新发现）" if spec.name in fresh else ""
        lines.append(f"- {spec.name}：{label}{mark}")
    return "\n".join(lines)
