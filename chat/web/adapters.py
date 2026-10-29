"""各联网服务的 HTTP 适配器；响应大小与错误信息统一收敛。"""

from __future__ import annotations

import json
import asyncio
from typing import Any
from dataclasses import dataclass

import httpx

from .models import SearchHit, PageResult, SearchResult, WebSearchError
from ...utils.http import read_limited, get_shared_async_client

MAX_RESPONSE_BYTES = 8 * 1024 * 1024


@dataclass
class WebServiceConfig:
    """一个联网服务的连接参数。"""

    name: str = ""
    adapter: str = "deepseek"
    base_url: str = ""
    api_key: str = ""
    timeout: float = 60.0
    search_depth: str = "basic"
    extract_depth: str = "basic"


class HTTPAdapter:
    """带大小限制的异步 JSON 请求，不重试、不切换供应商。"""

    def __init__(self, config: WebServiceConfig, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.config = config
        self.transport = transport

    def auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.config.api_key}"}

    def endpoint(self, action: str) -> str:
        raise NotImplementedError

    async def post(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.config.api_key.strip():
            raise WebSearchError("联网服务缺少 API Key")
        try:
            if self.transport is not None:
                async with httpx.AsyncClient(
                    timeout=self.config.timeout,
                    transport=self.transport,
                    follow_redirects=False,
                ) as client:
                    response = await client.post(endpoint, json=payload, headers=self.auth_headers())
                    if not response.is_success:
                        raise WebSearchError(f"联网服务返回 HTTP {response.status_code}")
                    data = response.content
                    if len(data) > MAX_RESPONSE_BYTES:
                        raise WebSearchError("联网服务响应超过 8 MiB 限制")
            else:
                client = await get_shared_async_client()
                async with client.stream(
                    "POST",
                    endpoint,
                    json=payload,
                    headers=self.auth_headers(),
                    timeout=self.config.timeout,
                    follow_redirects=False,
                ) as response:
                    if not response.is_success:
                        raise WebSearchError(f"联网服务返回 HTTP {response.status_code}")
                    data = await read_limited(
                        response.aiter_bytes(), response.headers.get("content-length"), MAX_RESPONSE_BYTES
                    )
        except httpx.TimeoutException:
            raise WebSearchError("联网服务请求超时") from None
        except httpx.HTTPError as exc:
            raise WebSearchError(f"联网服务连接失败：{type(exc).__name__}") from None
        try:
            result = await asyncio.to_thread(json.loads, data)
        except (ValueError, UnicodeError):
            raise WebSearchError("联网服务返回了无效 JSON") from None
        if not isinstance(result, dict):
            raise WebSearchError("联网服务响应必须是 JSON 对象")
        if "error" in result:
            raise WebSearchError("联网服务返回业务错误")
        return result

    async def search(self, query: str, max_results: int) -> SearchResult:
        raise NotImplementedError

    async def fetch(self, url: str) -> PageResult:
        raise NotImplementedError


class DeepSeekAdapter(HTTPAdapter):
    """DeepSeek Search / Fetch API。"""

    def endpoint(self, action: str) -> str:
        base = (self.config.base_url or "https://api.deepseek.com").rstrip("/")
        if not base.endswith("/v1"):
            base += "/v1"
        return f"{base}/tools/{action}"

    async def search(self, query: str, max_results: int) -> SearchResult:
        if len(query) > 128:
            raise WebSearchError("DeepSeek 搜索词不能超过 128 个字符，请缩短后重试")
        data = await self.post(
            self.endpoint("search"),
            {"query": query, "count": max_results, "need_content": False},
        )
        return SearchResult(
            query=query,
            results=[
                SearchHit(
                    title=item["title"],
                    url=item["url"],
                    snippet=item["snippet"],
                    summary=item.get("summary", ""),
                    published_at=item.get("date"),
                )
                for item in data["results"][:max_results]
            ],
        )

    async def fetch(self, url: str) -> PageResult:
        data = await self.post(self.endpoint("fetch"), {"url": url})
        return PageResult(url=url, title=data["title"], content=data["content"])


class TavilyAdapter(HTTPAdapter):
    """Tavily Search / Extract API。"""

    def endpoint(self, action: str) -> str:
        return f"{(self.config.base_url or 'https://api.tavily.com').rstrip('/')}/{action}"

    async def search(self, query: str, max_results: int) -> SearchResult:
        data = await self.post(
            self.endpoint("search"),
            {
                "query": query,
                "max_results": max_results,
                "search_depth": self.config.search_depth,
                "include_answer": False,
                "include_raw_content": False,
            },
        )
        return SearchResult(
            query=query,
            results=[
                SearchHit(
                    title=item["title"],
                    url=item["url"],
                    snippet=item["content"],
                    published_at=item.get("published_date"),
                )
                for item in data["results"][:max_results]
            ],
        )

    async def fetch(self, url: str) -> PageResult:
        data = await self.post(
            self.endpoint("extract"),
            {"urls": [url], "extract_depth": self.config.extract_depth, "format": "markdown"},
        )
        if data.get("failed_results") or not data["results"]:
            raise WebSearchError("Tavily 未能提取指定网页的正文")
        item = data["results"][0]
        return PageResult(url=item["url"], title=item.get("title", ""), content=item["raw_content"])


class AnySearchAdapter(HTTPAdapter):
    """AnySearch v1 Search / Extract API。"""

    def endpoint(self, action: str) -> str:
        base = (self.config.base_url or "https://api.anysearch.com").rstrip("/")
        return f"{base if base.endswith('/v1') else base + '/v1'}/{action}"

    async def search(self, query: str, max_results: int) -> SearchResult:
        if max_results > 10:
            raise WebSearchError("AnySearch 的 max_results 不能超过 10")
        data = await self.post(self.endpoint("search"), {"query": query, "max_results": max_results})
        if data["code"] != 0:
            raise WebSearchError("AnySearch 返回业务错误")
        return SearchResult(
            query=query,
            results=[
                SearchHit(title=item["title"], url=item["url"], snippet=item.get("snippet", ""))
                for item in data["data"]["results"][:max_results]
            ],
        )

    async def fetch(self, url: str) -> PageResult:
        data = await self.post(self.endpoint("extract"), {"url": url})
        if data["code"] != 0:
            raise WebSearchError("AnySearch 未能提取指定网页的正文")
        item = data["data"]
        return PageResult(url=item["url"], title=item["title"], content=item["content"])


class ExaAdapter(HTTPAdapter):
    """Exa Search / Contents API。"""

    def auth_headers(self) -> dict[str, str]:
        return {"x-api-key": self.config.api_key}

    def endpoint(self, action: str) -> str:
        return f"{(self.config.base_url or 'https://api.exa.ai').rstrip('/')}/{action}"

    async def search(self, query: str, max_results: int) -> SearchResult:
        data = await self.post(
            self.endpoint("search"),
            {"query": query, "numResults": max_results, "type": "auto", "contents": {"highlights": True}},
        )
        return SearchResult(
            query=query,
            results=[
                SearchHit(
                    title=item["title"],
                    url=item["url"],
                    snippet="\n".join(item.get("highlights", [])),
                    summary=item.get("summary", ""),
                    published_at=item.get("publishedDate"),
                )
                for item in data["results"][:max_results]
            ],
        )

    async def fetch(self, url: str) -> PageResult:
        data = await self.post(self.endpoint("contents"), {"urls": [url], "text": True})
        if not data["results"] or any(item["status"] != "success" for item in data.get("statuses", [])):
            raise WebSearchError("Exa 未能提取指定网页的正文")
        item = data["results"][0]
        return PageResult(url=item["url"], title=item["title"], content=item["text"])


class FirecrawlAdapter(HTTPAdapter):
    """Firecrawl v2 Search / Scrape API。"""

    def endpoint(self, action: str) -> str:
        base = (self.config.base_url or "https://api.firecrawl.dev").rstrip("/")
        return f"{base if base.endswith('/v2') else base + '/v2'}/{action}"

    async def search(self, query: str, max_results: int) -> SearchResult:
        if len(query) > 500:
            raise WebSearchError("Firecrawl 的 query 不能超过 500 个字符")
        data = await self.post(
            self.endpoint("search"),
            {"query": query, "limit": max_results, "sources": ["web"]},
        )
        if data["success"] is not True:
            raise WebSearchError("Firecrawl 返回业务错误")
        return SearchResult(
            query=query,
            results=[
                SearchHit(title=item["title"], url=item["url"], snippet=item["description"])
                for item in data["data"]["web"][:max_results]
            ],
        )

    async def fetch(self, url: str) -> PageResult:
        data = await self.post(self.endpoint("scrape"), {"url": url, "formats": ["markdown"]})
        if data["success"] is not True:
            raise WebSearchError("Firecrawl 未能提取指定网页的正文")
        item = data["data"]
        return PageResult(url=url, title=item.get("metadata", {}).get("title", ""), content=item["markdown"])


ADAPTERS: dict[str, type[HTTPAdapter]] = {
    "deepseek": DeepSeekAdapter,
    "tavily": TavilyAdapter,
    "anysearch": AnySearchAdapter,
    "exa": ExaAdapter,
    "firecrawl": FirecrawlAdapter,
}
