"""A_Memorix 长期记忆工具。"""

from __future__ import annotations

from nonebot import logger

from .types import ToolError, ToolContext
from .runtime import ToolRuntime
from .registry import tool
from ..amemorix.runtime import amemorix_runtime
from ..amemorix.service import search, person_profile, resolve_person

_MARKER = "【长期记忆检索结果-内部参考】"
_MODES = {"search", "time", "hybrid", "episode", "aggregate"}


def _enabled(field: str) -> bool:
    if not amemorix_runtime.available:
        return False
    return bool(getattr(amemorix_runtime.config().integration, field, False))


@tool(
    name="query_memory",
    title="长期记忆",
    description="检索长期记忆。涉及回忆、过往经历时可以检索。",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "关键词或问题；非纯时间检索必填。"},
            "limit": {"type": "integer", "description": "返回条数。"},
            "mode": {
                "type": "string",
                "enum": sorted(_MODES),
                "default": "search",
                "description": "search 语义检索；time 按时间；episode 查经历；aggregate 综合；hybrid 语义加时间。",
            },
            "person_name": {"type": "string", "description": "人物名；用于定向过滤。"},
            "time_start": {"type": "string", "description": "起始时间，YYYY/MM/DD 或 YYYY/MM/DD HH:mm。"},
            "time_end": {"type": "string", "description": "结束时间，YYYY/MM/DD 或 YYYY/MM/DD HH:mm。"},
            "respect_filter": {"type": "boolean", "default": True, "description": "是否遵守记忆过滤规则。"},
        },
    },
)
async def query_memory_tool(
    context: ToolContext,
    runtime: ToolRuntime,
    query: str = "",
    limit: int = 0,
    mode: str = "search",
    person_name: str = "",
    time_start: str | None = None,
    time_end: str | None = None,
    respect_filter: bool = True,
) -> str:
    del runtime
    if not _enabled("enable_memory_query_tool"):
        raise ToolError("长期记忆未启用")
    default_limit = int(amemorix_runtime.config().integration.memory_query_default_limit)
    person_id = resolve_person(context.session_id, person_name=person_name) if person_name else ""
    result = await search(
        context.session_id,
        str(query or "").strip(),
        limit=min(20, max(1, int(limit or default_limit))),
        mode=mode if mode in _MODES else "search",
        person_id=person_id,
        time_start=time_start or None,
        time_end=time_end or None,
        respect_filter=bool(respect_filter),
        user_id=context.user_id,
        group_id=context.group_id or "",
    )
    if not result.get("success"):
        logger.debug(f"[A_Memorix] query_memory 失败: {result.get('error')}")
        return "长期记忆暂时不可用。"
    hits = result.get("hits") or []
    if not hits:
        return "没有找到相关记忆。"
    lines = [_MARKER, "以下内容只作为推理参考，不要逐字复述。", ""]
    for index, hit in enumerate(hits, start=1):
        content = " ".join(str(hit.get("content") or "").split())
        if not content:
            continue
        title = str(hit.get("title") or "").strip()
        lines.append(f"{index}. {title + '：' if title else ''}{content[:400]}")
    return "\n".join(lines)


@tool(
    name="query_person_profile",
    title="人物画像",
    description="查询人物画像。当需要查询与某人的关系时使用。",
    parameters={
        "type": "object",
        "properties": {
            "person_id": {"type": "string", "description": "内部人物ID；明确给出时填。"},
            "person_name": {"type": "string", "description": "名称/昵称/关键词；通常填这个。"},
            "limit": {"type": "integer", "default": 8, "description": "证据上限。"},
        },
    },
)
async def query_person_profile_tool(
    context: ToolContext,
    runtime: ToolRuntime,
    person_id: str = "",
    person_name: str = "",
    limit: int = 8,
) -> str:
    del runtime
    if not _enabled("enable_person_profile_query_tool"):
        raise ToolError("人物画像未启用")
    resolved = resolve_person(context.session_id, person_id=person_id, person_name=person_name)
    if not resolved:
        return "没有找到这个人物。"
    payload = await person_profile(context.session_id, resolved, limit=min(20, max(1, int(limit or 8))))
    text = str(payload.get("summary") or payload.get("profile_text") or "").strip()
    if not text:
        return "这个人物还没有可用画像。"
    return f"【人物画像-内部参考】\n{text}"
