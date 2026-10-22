"""内置联网搜索工具。"""

from __future__ import annotations

import time
from typing import Any

import httpx

from .types import ToolError, ToolContext
from .runtime import ToolRuntime
from ...config import get_manager
from .registry import tool
from ...utils.http import get_shared_async_client

_MAX_QUERY_CHARS = 128
_COUNT = 5
_TIMEOUT = 20.0
_COOLDOWN = 3.0
_cooldowns: dict[str, float] = {}


def _config() -> dict[str, Any]:
    """读取联网搜索配置。"""
    return get_manager().get_system()["chat"]["web_search"]


def _search_url(base_url: str) -> str:
    """拼出 Search 接口地址，兼容误填 /v1 的地址。"""
    base = str(base_url or "").strip().rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    return f"{base}/v1/tools/search"


def _format_results(results: list[dict[str, Any]]) -> str:
    """把搜索结果压成模型易读的紧凑文本。"""
    blocks: list[str] = []
    for index, item in enumerate(results, 1):
        title = " ".join(str(item.get("title") or "").split()) or "(无标题)"
        link = str(item.get("url") or "").strip()
        summary = " ".join(str(item.get("summary") or item.get("snippet") or "").split())
        blocks.append(f"{index}. {title}\n{link}\n{summary}")
    return "\n\n".join(blocks)


@tool(
    name="web_search",
    title="联网搜索",
    description="联网搜索实时信息，返回标题、链接和摘要。需要新闻、价格、版本等最新资料时使用。",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词，支持 site: 等语法"},
        },
        "required": ["query"],
    },
)
async def web_search_tool(context: ToolContext, runtime: ToolRuntime, query: str) -> str:
    """调用 DeepSeek Search 接口搜索网页。"""
    cfg = _config()
    api_key = str(cfg["api_key"] or "").strip()
    if not api_key:
        raise ToolError("联网搜索未配置 API Key，暂不可用")

    value = " ".join(str(query or "").split())[:_MAX_QUERY_CHARS]
    if not value:
        raise ToolError("搜索关键词不能为空")

    session = context.session_id or context.user_id or "default"
    now = time.monotonic()
    remaining = float(_cooldowns.get(session, 0)) + _COOLDOWN - now
    if remaining > 0:
        raise ToolError(f"搜索太频繁，请 {int(remaining) + 1} 秒后再试")
    _cooldowns[session] = now

    try:
        client = await get_shared_async_client()
        response = await client.post(
            _search_url(str(cfg["base_url"] or "")),
            json={"query": value, "count": _COUNT, "need_content": False},
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise ToolError("搜索请求失败，请稍后再试") from exc

    if response.status_code == 401:
        raise ToolError("搜索 API Key 无效")
    if response.status_code == 402:
        raise ToolError("搜索 API 余额不足")
    if response.status_code == 429:
        raise ToolError("搜索服务繁忙，请稍后再试")
    if response.status_code >= 400:
        raise ToolError(f"搜索失败（HTTP {response.status_code}）")

    try:
        results = response.json().get("results") or []
    except ValueError as exc:
        raise ToolError("搜索返回格式异常") from exc
    return _format_results(results) if results else "没有找到相关结果"
