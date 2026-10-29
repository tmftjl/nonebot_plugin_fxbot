"""联网服务：配置快照、适配器与服务调用。"""

from .config import web_runtime
from .service import WebRuntimeConfig, WebSearchService, result_text
from .adapters import ADAPTERS, WebServiceConfig

__all__ = [
    "ADAPTERS",
    "WebRuntimeConfig",
    "WebSearchService",
    "WebServiceConfig",
    "result_text",
    "web_runtime",
]
