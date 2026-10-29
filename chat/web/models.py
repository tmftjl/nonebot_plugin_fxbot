"""联网服务的统一结果与可安全返回给模型的错误。"""

from __future__ import annotations

from typing import Optional
from dataclasses import field, dataclass


class WebSearchError(Exception):
    """可以安全返回模型的联网错误，不包含密钥或上游响应体。"""


@dataclass
class SearchHit:
    title: str
    url: str
    snippet: str
    summary: str = ""
    published_at: Optional[str] = None


@dataclass
class SearchResult:
    query: str
    results: list[SearchHit] = field(default_factory=list)
    content_kind: str = "search_results"


@dataclass
class PageResult:
    url: str
    title: str
    content: str
    content_kind: str = "page_text"
