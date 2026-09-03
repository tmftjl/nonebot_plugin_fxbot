"""共享 HTTP 客户端辅助函数。"""

from __future__ import annotations

from collections.abc import Mapping

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


async def get_text_with_browser_fallback(
    url: str,
    *,
    timeout: float,
    follow_redirects: bool = True,
    headers: Mapping[str, str] | None = None,
) -> str:
    """获取文本响应，传输层失败时使用浏览器 TLS 指纹重试。"""
    try:
        client = await get_shared_async_client()
        response = await client.get(
            url, timeout=timeout, follow_redirects=follow_redirects, headers=headers
        )
        response.raise_for_status()
        return response.text
    except httpx.TransportError as exc:
        return await _get_text_with_curl(
            url,
            timeout=timeout,
            follow_redirects=follow_redirects,
            headers=headers,
            cause=exc,
        )


async def _get_text_with_curl(
    url: str,
    *,
    timeout: float,
    follow_redirects: bool,
    headers: Mapping[str, str] | None,
    cause: Exception,
) -> str:
    """使用 curl_cffi 的浏览器 TLS 指纹获取文本。"""
    try:
        import curl_cffi
    except Exception as exc:  # pragma: no cover - 依赖缺失只会出现在运行环境
        raise RuntimeError("HTTP 请求失败，且缺少 curl_cffi 依赖，无法使用浏览器 TLS 兜底") from exc

    try:
        async with curl_cffi.AsyncSession(
            allow_redirects=follow_redirects,
            impersonate="chrome",
            default_encoding="utf-8",
        ) as session:
            response = await session.get(url, headers=dict(headers or {}), timeout=timeout)
            response.raise_for_status()
            return response.text
    except Exception as exc:
        raise RuntimeError(f"HTTP 请求失败，浏览器 TLS 兜底请求也未成功：{exc}") from cause
