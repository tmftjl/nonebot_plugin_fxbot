"""联网服务调用入口：校验参数、限制时限并规范化结果。"""

from __future__ import annotations

import asyncio
from typing import Union
from dataclasses import field, dataclass
from urllib.parse import urlsplit

import httpx

from .models import PageResult, SearchResult, WebSearchError
from .adapters import ADAPTERS, HTTPAdapter, WebServiceConfig

MAX_QUERY_CHARS = 1000
MAX_RESULTS = 20


@dataclass
class WebRuntimeConfig:
    """一次调用使用的配置快照。"""

    preview_chars: int = 4000
    services: dict[str, WebServiceConfig] = field(default_factory=dict)
    selected: str = ""

    @property
    def enabled(self) -> bool:
        return bool(self.selected and self.service is not None)

    @property
    def service(self) -> WebServiceConfig | None:
        return self.services.get(self.selected)


def validate_url(url: str) -> str:
    """只接受无嵌入凭据的 HTTP(S) URL。"""
    if not isinstance(url, str) or not url.strip() or len(url) > 8192:
        raise WebSearchError("url 必须是非空字符串且不超过 8192 个字符")
    url = url.strip()
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise WebSearchError("url 必须是无用户名和密码的 HTTP(S) 地址")
    return url


class WebSearchService:
    """按适配器执行搜索或网页读取。"""

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    def _adapter(self, settings: WebServiceConfig) -> HTTPAdapter:
        adapter_class = ADAPTERS.get(settings.adapter)
        if adapter_class is None:
            raise WebSearchError(f"不支持的联网服务协议：{settings.adapter}")
        return adapter_class(settings, self.transport)

    async def search(self, config: WebRuntimeConfig, query: str, max_results: int = 5) -> SearchResult:
        settings = config.service
        if settings is None:
            raise WebSearchError("当前操作未配置联网服务")
        value = str(query or "").strip()
        if not value or len(value) > MAX_QUERY_CHARS:
            raise WebSearchError(f"query 必须是非空字符串且不超过 {MAX_QUERY_CHARS} 个字符")
        if type(max_results) is not int or not 1 <= max_results <= MAX_RESULTS:
            raise WebSearchError(f"max_results 必须是 1 到 {MAX_RESULTS} 的整数")
        adapter = self._adapter(settings)
        try:
            async with asyncio.timeout(settings.timeout):
                result = await adapter.search(value, max_results)
        except TimeoutError:
            raise WebSearchError(f"联网服务调用超过 {settings.timeout:g} 秒") from None
        except (KeyError, TypeError, ValueError, AttributeError):
            raise WebSearchError(f"{settings.adapter} 返回数据不符合接口协议") from None
        for item in result.results:
            validate_url(item.url)
            if not all(isinstance(v, str) for v in (item.title, item.snippet, item.summary)):
                raise WebSearchError("搜索服务返回了无效文本字段")
            if item.published_at is not None and not isinstance(item.published_at, str):
                raise WebSearchError("搜索服务返回了无效日期字段")
        return result

    async def fetch(self, config: WebRuntimeConfig, url: str) -> PageResult:
        settings = config.service
        if settings is None:
            raise WebSearchError("当前操作未配置联网服务")
        target = validate_url(url)
        adapter = self._adapter(settings)
        try:
            async with asyncio.timeout(settings.timeout):
                page = await adapter.fetch(target)
        except TimeoutError:
            raise WebSearchError(f"联网服务调用超过 {settings.timeout:g} 秒") from None
        except (KeyError, TypeError, ValueError, AttributeError):
            raise WebSearchError(f"{settings.adapter} 返回数据不符合接口协议") from None
        validate_url(page.url)
        if not isinstance(page.title, str) or not isinstance(page.content, str) or not page.content.strip():
            raise WebSearchError("网页读取服务没有返回有效正文")
        return page


def result_text(result: Union[SearchResult, PageResult]) -> str:
    """把结果序列化为写入工具结果的原文。"""
    if isinstance(result, PageResult):
        return f"{result.title}\n{result.url}\n{result.content}"
    lines: list[str] = [f"搜索：{result.query}"]
    for index, item in enumerate(result.results, 1):
        lines.append(f"{index}. {item.title}\n{item.url}\n{item.summary or item.snippet}")
    return "\n\n".join(lines)
