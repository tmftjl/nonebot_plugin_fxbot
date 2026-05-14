"""共享 HTTP 客户端辅助函数。"""

from __future__ import annotations

import httpx

_client: httpx.AsyncClient | None = None


async def get_shared_async_client() -> httpx.AsyncClient:
    """获取共享异步 HTTP 客户端。"""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=httpx.Timeout(15.0))
    return _client


async def close_shared_async_client() -> None:
    """关闭共享异步 HTTP 客户端。"""
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None
