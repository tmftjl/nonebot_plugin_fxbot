"""长期抽取的噪声过滤与注入文本净化。"""

from __future__ import annotations

import re
import time
import hashlib
import unicodedata

from ...config import get_manager as get_config_manager

# 敏感信息统一在过滤层判定，学习模块共用。
SENSITIVE = re.compile(
    r"(密码|口令|token|secret|api[_ ]?key|身份证|银行卡|信用卡|手机号|\b1[3-9]\d{9}\b|住址)",
    re.I,
)

_URL_ONLY = re.compile(r"^(?:https?://\S+\s*)+$", re.I)
_AD = re.compile(r"(加群|代练|刷单|返利|优惠券|免费领取|vx[:：]?\s*\w+|微信[:：]?\s*\w+|qq群[:：]?\s*\d+)", re.I)
_RECENT_TTL = 600.0
_recent_hashes: dict[tuple[str, str], float] = {}


def reset_recent_hashes() -> None:
    """清空重复消息缓存，供测试使用。"""
    _recent_hashes.clear()


def _command_prefixes() -> tuple[str, ...]:
    """读取当前命令前缀；配置不可用时退回常见前缀。"""
    try:
        value = get_config_manager().get_system()["chat"]["command_prefixes"]
        prefixes = tuple(str(item) for item in value if str(item))
    except Exception:
        prefixes = ("#", "/", "gs", "ww")
    return prefixes + ("/",) if "/" not in prefixes else prefixes


def _digest(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _is_duplicate(tenant: str, text: str, now: float) -> bool:
    """十分钟内同租户相同内容只学一次。"""
    last = _recent_hashes.get((tenant, _digest(text)))
    return last is not None and now - last < _RECENT_TTL


def mark_learned(texts: list[str], *, tenant: str = "", now: float | None = None) -> None:
    """批次成功提交后才登记内容；失败重试时仍可再次抽取。"""
    moment = time.monotonic() if now is None else now
    for text in texts:
        content = " ".join(str(text or "").split())
        if content:
            _recent_hashes[(tenant, _digest(content))] = moment
    if len(_recent_hashes) > 4096:
        cutoff = moment - _RECENT_TTL
        for candidate in [item for item, seen in _recent_hashes.items() if seen < cutoff]:
            _recent_hashes.pop(candidate, None)


def _visible_chars(text: str) -> int:
    """统计去标点、符号后的有效字符数。"""
    return sum(1 for char in text if unicodedata.category(char)[0] not in {"P", "S", "Z", "C"})


def should_learn(text: str, *, tenant: str = "", now: float | None = None, check_duplicate: bool = True) -> bool:
    """判断一条人类消息是否值得进入长期抽取；回填只看内容噪声。"""
    content = " ".join(str(text or "").split())
    if not content or _visible_chars(content) < 2:
        return False
    if content.startswith(_command_prefixes()):
        return False
    if _URL_ONLY.match(content):
        return False
    if _AD.search(content):
        return False
    if not check_duplicate:
        return True
    return not _is_duplicate(tenant, content, time.monotonic() if now is None else now)


def sanitize_line(text: object, limit: int = 120) -> str:
    """把不可信记忆压成单行普通文本，去掉控制字符与方括号。"""
    value = "".join(char for char in str(text or "") if unicodedata.category(char)[0] != "C")
    value = " ".join(value.split()).replace("[", "【").replace("]", "】")
    return value[:limit]
