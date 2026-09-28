"""共享 HTTP 客户端辅助函数。"""

from __future__ import annotations

from collections.abc import Mapping, AsyncIterator

import httpx

_client: httpx.AsyncClient | None = None


class ResponseTooLargeError(RuntimeError):
    """响应体超出调用方给出的上限。"""


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
        response = await client.get(url, timeout=timeout, follow_redirects=follow_redirects, headers=headers)
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


async def get_bytes_with_browser_fallback(
    url: str,
    *,
    timeout: float,
    follow_redirects: bool = True,
    headers: Mapping[str, str] | None = None,
    max_bytes: int | None = None,
) -> bytes:
    """获取二进制响应，失败时使用浏览器 TLS 指纹重试。

    与文本版的两点差别都来自实际站点行为：
    按 ``httpx.HTTPError`` 兜底，因为有些站点对非浏览器客户端返回的是挑战页
    （HTTP 状态错误）而不是传输层错误；``impersonate`` 必须显式指定，默认指纹
    同样会被这类站点拒绝。``max_bytes`` 为 None 时不做大小限制。
    """
    try:
        client = await get_shared_async_client()
        async with client.stream(
            "GET",
            url,
            timeout=timeout,
            follow_redirects=follow_redirects,
            headers=headers,
        ) as response:
            response.raise_for_status()
            return await _read_limited(response.aiter_bytes(), response.headers.get("content-length"), max_bytes)
    except httpx.HTTPError as exc:
        return await _get_bytes_with_curl(
            url,
            timeout=timeout,
            follow_redirects=follow_redirects,
            headers=headers,
            max_bytes=max_bytes,
            cause=exc,
        )


async def _get_bytes_with_curl(
    url: str,
    *,
    timeout: float,
    follow_redirects: bool,
    headers: Mapping[str, str] | None,
    max_bytes: int | None,
    cause: Exception,
) -> bytes:
    """使用 curl_cffi 的浏览器 TLS 指纹获取二进制响应。"""
    try:
        import curl_cffi
    except Exception as exc:  # pragma: no cover - 依赖缺失只会出现在运行环境
        raise RuntimeError("HTTP 请求失败，且缺少 curl_cffi 依赖，无法使用浏览器 TLS 兜底") from exc

    try:
        async with curl_cffi.AsyncSession(allow_redirects=follow_redirects, impersonate="chrome") as session:
            response = await session.get(url, headers=dict(headers or {}), timeout=timeout, stream=True)
            response.raise_for_status()
            return await _read_limited(
                response.aiter_content(chunk_size=1024 * 1024),
                response.headers.get("content-length"),
                max_bytes,
            )
    except ResponseTooLargeError:
        # 大小超限是调用方的策略决定，不是请求失败，别被下面的兜底包装成网络错误
        raise
    except Exception as exc:
        raise RuntimeError(f"HTTP 请求失败，浏览器 TLS 兜底请求也未成功：{exc}") from cause


async def _read_limited(
    chunks: AsyncIterator[bytes],
    content_length: str | None,
    max_bytes: int | None,
) -> bytes:
    """分块读取响应体并在超限时中止，避免被超大响应撑爆内存。"""
    if max_bytes is not None and content_length:
        try:
            declared = int(content_length)
        except ValueError:
            declared = 0
        if declared > max_bytes:
            raise ResponseTooLargeError(f"响应体超出大小上限：{declared} > {max_bytes}")

    buffer = bytearray()
    async for chunk in chunks:
        buffer.extend(chunk)
        if max_bytes is not None and len(buffer) > max_bytes:
            raise ResponseTooLargeError(f"响应体超出大小上限：已读 {len(buffer)} > {max_bytes}")
    return bytes(buffer)
