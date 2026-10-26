"""触发回复的防复读检测与最近回复缓存。

参考 MaiBot 的表达复读判定：只比较最近 5 条实际发出的回复，
rapidfuzz `ratio`（0–100）达到阈值时重写一次；连续同开场词也重写。
"""

from __future__ import annotations

import re
from typing import Any

from rapidfuzz import fuzz

from ...config import get_manager as get_config_manager
from ...utils.cache import cache

_DOMAIN = "chat:anti_repeat"
_MAX_RECENT = 5
_OPENING_TRIM = re.compile(r"^[\s\W_]+", re.UNICODE)
_OPENING_SPLIT = re.compile(r"[，。！？!?,.、;；:：\n]")


def _key(tenant: str) -> tuple[str, str]:
    """防复读缓存按租户隔离。"""
    return (_DOMAIN, tenant)


def _settings() -> tuple[bool, int]:
    """每次读取当前配置；非法值回退默认。"""
    try:
        behavior = get_config_manager().get_system()["chat"].get("behavior") or {}
    except Exception:
        behavior = {}
    enabled = behavior.get("anti_repeat", True)
    threshold = behavior.get("anti_repeat_threshold", 85)
    if not isinstance(enabled, bool):
        enabled = True
    if isinstance(threshold, bool) or not isinstance(threshold, int):
        threshold = 85
    return enabled, max(0, min(100, threshold))


def _normalize(text: Any) -> str:
    """压缩空白，避免排版差异影响相似度。"""
    return " ".join(str(text or "").split())


def recent_replies(tenant: str) -> list[str]:
    """返回最近发出的回复，最新在后。"""
    value = cache.get(_key(tenant))
    return list(value) if isinstance(value, list) else []


def remember_reply(tenant: str, text: str) -> None:
    """只在消息真正发出后登记，生成但未发送的内容不参与防复读。"""
    content = _normalize(text)
    if not tenant or not content:
        return
    values = recent_replies(tenant)
    values.append(content)
    cache.set(_key(tenant), values[-_MAX_RECENT:])


def forget_replies(tenant: str) -> None:
    """清空会话时一并清掉防复读上下文。"""
    cache.clear_prefix(_key(tenant))


def _opening(text: str) -> str:
    """取第一个短句作为开场词，去掉前导标点和表情符号。"""
    value = _OPENING_TRIM.sub("", _normalize(text))
    return _OPENING_SPLIT.split(value, maxsplit=1)[0][:12].casefold()


def rewrite_reason(tenant: str, text: str) -> str:
    """返回需要重写的原因：repeat、opener，或无需重写。"""
    enabled, threshold = _settings()
    content = _normalize(text)
    if not enabled or not content:
        return ""
    values = recent_replies(tenant)
    if not values:
        return ""
    if any(fuzz.ratio(content, previous) >= threshold for previous in values):
        return "repeat"
    opening = _opening(content)
    if opening and len(values) >= 2 and all(_opening(previous) == opening for previous in values[-2:]):
        return "opener"
    return ""


def rewrite_instruction(reason: str) -> str:
    """生成一次重写用的指令，不把内部判定名发给用户。"""
    if reason == "opener":
        return "你连续几条回复都用同一个开头。请重写这条回复，换一个不同的开头，保持原意和人格。只输出新回复。"
    return "你刚才的回复和最近说过的话太像了。请换个说法重写这条回复，保持原意和人格，不要重复上一句。只输出新回复。"
