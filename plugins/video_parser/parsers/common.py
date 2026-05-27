"""平台解析通用工具。"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from ..config import cfg_general, cfg_network
from .base import ParseError

COMMON_HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def timeout() -> httpx.Timeout:
    """构造 HTTP 超时。"""
    return httpx.Timeout(float(cfg_general().get("request_timeout_seconds", 20)))


def proxy() -> str | None:
    """读取代理。"""
    value = str(cfg_network().get("proxy") or "").strip()
    return value or None


async def get_text(url: str, *, headers: dict[str, str] | None = None, follow_redirects: bool = True) -> str:
    """请求文本内容。"""
    async with httpx.AsyncClient(timeout=timeout(), proxy=proxy(), follow_redirects=follow_redirects, verify=False) as client:
        response = await client.get(url, headers=headers or COMMON_HEADERS)
        response.raise_for_status()
        return response.text


async def final_url(url: str, *, headers: dict[str, str] | None = None) -> str:
    """获取最终跳转地址。"""
    async with httpx.AsyncClient(timeout=timeout(), proxy=proxy(), follow_redirects=True, verify=False) as client:
        response = await client.get(url, headers=headers or COMMON_HEADERS)
        response.raise_for_status()
        return str(response.url)


def extract_json(html: str, pattern: str, *, undefined_to_null: bool = False) -> Any:
    """从 HTML 中提取脚本 JSON。"""
    matched = re.search(pattern, html, re.S)
    if not matched:
        raise ParseError("页面中没有找到视频数据")
    raw = matched.group(1).strip().rstrip(";")
    if undefined_to_null:
        raw = raw.replace("undefined", "null")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ParseError("页面视频数据不是有效 JSON") from exc


def first(value: Any) -> Any:
    """取列表首项。"""
    return value[0] if isinstance(value, list) and value else None
