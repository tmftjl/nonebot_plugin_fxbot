"""视频平台解析入口。"""

from __future__ import annotations

from .base import ParseError, can_parse_url, find_url, parse_url

__all__ = ["ParseError", "can_parse_url", "find_url", "parse_url"]
