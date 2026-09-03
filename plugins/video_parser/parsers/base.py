"""视频平台解析调度。"""

from __future__ import annotations

import html
import re
from urllib.parse import unquote

from ..config import cfg_platforms
from ..types import VideoResult

URL_RE = re.compile(
    r"https?://[^\s<>\"']+|"
    r"(?:v\.douyin|jx\.douyin|jingxuan\.douyin|v\.kuaishou|xhslink)\.com/[A-Za-z0-9._?%&+=/#@-]+|"
    r"(?:v\.m\.)?chenzhongtech\.com/fw/[A-Za-z0-9._?%&+=/#@-]+|"
    r"mapp\.api\.weibo\.cn/fx/[A-Za-z0-9]+\.html|"
    r"(?:b23\.tv|bili2233\.cn)/[A-Za-z0-9._?%&+=/#@-]+"
)
SUPPORTED_URL_MARKERS = (
    "douyin.com",
    "iesdouyin.com",
    "jingxuan.douyin.com",
    "kuaishou.com",
    "chenzhongtech.com",
    "xiaohongshu.com",
    "xhslink.com",
    "weibo.com",
    "weibo.cn",
    "mapp.api.weibo.cn",
    "card.weibo.com",
    "bilibili.com",
    "b23.tv",
    "bili2233.cn",
)


class ParseError(RuntimeError):
    """解析失败。"""


def find_url(text: str) -> str | None:
    """从文本中寻找候选链接。"""
    variants = _text_variants(text)
    for candidate in variants:
        for matched in URL_RE.finditer(candidate):
            url = matched.group(0).strip().rstrip("，。；;)")
            url = url if url.startswith("http") else f"https://{url}"
            if _is_supported_url(url):
                return url
    bv = re.search(r"\bBV[0-9A-Za-z]{10}\b", text)
    if bv:
        return bv.group(0)
    av = re.search(r"\bav\d{6,}\b", text, re.I)
    if av:
        return av.group(0)
    return None


def _text_variants(text: str) -> tuple[str, ...]:
    """生成常见卡片转义文本变体。"""
    first = html.unescape(text).replace("\\/", "/")
    second = unquote(first)
    third = unquote(second)
    return tuple(dict.fromkeys((first, second, third)))


def _is_supported_url(url: str) -> bool:
    """判断 URL 是否属于已支持的平台。"""
    lower = url.lower()
    return any(marker in lower for marker in SUPPORTED_URL_MARKERS)


def can_parse_url(url: str) -> bool:
    """判断 URL 当前是否可以解析。"""
    return _match_enabled_platform(url) is not None


def _match_enabled_platform(url: str) -> str | None:
    """匹配当前启用的平台。"""
    platforms = cfg_platforms()
    lower = url.lower()

    if platforms.get("douyin", True) and ("douyin.com" in lower or "iesdouyin.com" in lower):
        return "douyin"
    if platforms.get("kuaishou", True) and (
        "kuaishou.com" in lower or "chenzhongtech.com" in lower
    ):
        return "kuaishou"
    if platforms.get("xiaohongshu", True) and (
        "xiaohongshu.com" in lower or "xhslink.com" in lower
    ):
        return "xiaohongshu"
    if platforms.get("weibo", True) and ("weibo.com" in lower or "weibo.cn" in lower):
        return "weibo"
    if platforms.get("bilibili", True) and (
        "bilibili.com" in lower
        or "b23.tv" in lower
        or "bili2233.cn" in lower
        or re.fullmatch(r"BV[0-9A-Za-z]{10}", url)
        or re.fullmatch(r"av\d{6,}", url, re.I)
    ):
        return "bilibili"
    return None


async def parse_url(url: str) -> VideoResult:
    """按平台解析链接。"""
    platform = _match_enabled_platform(url)

    if platform == "douyin":
        from .douyin import parse

        return await parse(url)
    if platform == "kuaishou":
        from .kuaishou import parse

        return await parse(url)
    if platform == "xiaohongshu":
        from .xiaohongshu import parse

        return await parse(url)
    if platform == "weibo":
        from .weibo import parse

        return await parse(url)
    if platform == "bilibili":
        from .bilibili import parse

        return await parse(url)

    raise ParseError("没有启用的平台能处理该链接")
