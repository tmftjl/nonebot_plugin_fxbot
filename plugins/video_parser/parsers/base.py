"""视频平台解析调度。"""

from __future__ import annotations

import re

from ..config import cfg_platforms
from ..types import VideoResult

URL_RE = re.compile(r"https?://[^\s<>\"']+|(?:v\.douyin|jx\.douyin|v\.kuaishou|xhslink)\.com/[A-Za-z0-9._?%&+=/#@-]+|b23\.tv/[A-Za-z0-9._?%&+=/#@-]+")


class ParseError(RuntimeError):
    """解析失败。"""


def find_url(text: str) -> str | None:
    """从文本中寻找候选链接。"""
    matched = URL_RE.search(text)
    if matched:
        url = matched.group(0).strip()
        return url if url.startswith("http") else f"https://{url}"
    bv = re.search(r"\bBV[0-9A-Za-z]{10}\b", text)
    if bv:
        return bv.group(0)
    return None


async def parse_url(url: str) -> VideoResult:
    """按平台解析链接。"""
    platforms = cfg_platforms()
    lower = url.lower()

    if platforms.get("douyin", True) and ("douyin.com" in lower or "iesdouyin.com" in lower):
        from .douyin import parse

        return await parse(url)
    if platforms.get("kuaishou", True) and ("kuaishou.com" in lower or "chenzhongtech.com" in lower):
        from .kuaishou import parse

        return await parse(url)
    if platforms.get("xiaohongshu", True) and ("xiaohongshu.com" in lower or "xhslink.com" in lower):
        from .xiaohongshu import parse

        return await parse(url)
    if platforms.get("weibo", True) and ("weibo.com" in lower or "weibo.cn" in lower):
        from .weibo import parse

        return await parse(url)
    if platforms.get("bilibili", True) and ("bilibili.com" in lower or "b23.tv" in lower or re.fullmatch(r"BV[0-9A-Za-z]{10}", url)):
        from .bilibili import parse

        return await parse(url)

    raise ParseError("没有启用的平台能处理该链接")
