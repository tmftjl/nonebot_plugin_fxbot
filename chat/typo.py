"""发送前错字模拟；历史仍保存干净文本。"""

from __future__ import annotations

import re
import random
from typing import Any
from functools import lru_cache

from pypinyin import Style, pinyin

from ..config import get_manager as get_config_manager
from .message_adapter import platform_for_bot

# 同音字库：同一拼音的不同声调、不同字
_POOL = (
    "的得在再是时使诗事市式妈麻马骂衣移椅意花滑画话华书熟属树数"
    "冤元远愿风逢讽凤张章掌丈长常场唱见件间简建想相向香有又右友"
    "好号毫爱哀挨看砍说硕多夺天甜填起气七来赖去取会回灰就旧酒"
    "没每美认任过国们门生声胜小笑校新心信明名命点电店里理力李"
    "上商伤下夏大打达中种重难南男了乐着这业夜还海孩最嘴罪很狠恨"
    "不布步要药摇吃迟尺完玩晚之只指做作坐到道倒"
)

# 词级错写：整词替换，语义不变
_WORDS = {
    "因为": "因位",
    "已经": "以经",
    "现在": "现再",
    "时候": "时后",
    "名字": "明字",
    "完成": "玩成",
    "心情": "新情",
}

_PROTECT = re.compile(
    r"```[\s\S]*?```|`[^`\n]*`|https?://\S+|@[^\s，。！？!?]+|#[^\s，。！？!?]+"
    r"|\d+(?:\.\d+)?|【[^】]*】|（[^）]*）|\([^)]*\)|\[[^\]]*\]"
)
_PLACEHOLDER = re.compile(r"\x00(\d+)\x00")
_SENTENCE = re.compile(r"([。！？!?；;\n]+)")
_CORRECTION = "（打错了，是 {text}）"
_CORRECTION_CHANCE = 0.5


@lru_cache(maxsize=2048)
def _read(ch: str) -> tuple[str, str]:
    """返回单字的无声调拼音与声调。"""
    values = pinyin(ch, style=Style.TONE3, errors="ignore")
    if not values or not values[0]:
        return "", ""
    syllable = values[0][0]
    return (syllable[:-1], syllable[-1]) if syllable[-1].isdigit() else (syllable, "5")


def _build() -> dict[str, dict[str, list[str]]]:
    """按拼音和声调归类同音字。"""
    groups: dict[str, dict[str, list[str]]] = {}
    for ch in dict.fromkeys(_POOL):
        base, tone = _read(ch)
        if base:
            groups.setdefault(base, {}).setdefault(tone, []).append(ch)
    return groups


_BY_BASE = _build()


def typo_config() -> dict[str, Any]:
    """读取错字配置；读取失败按关闭处理。"""
    try:
        value = get_config_manager().get_system()["chat"].get("typo") or {}
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _rate(config: dict[str, Any], key: str) -> float:
    value = config.get(key)
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def _mask(text: str) -> tuple[str, list[str]]:
    """把不改写的片段换成占位符。"""
    spans: list[str] = []

    def take(match: re.Match[str]) -> str:
        spans.append(match.group(0))
        return f"\x00{len(spans) - 1}\x00"

    return _PROTECT.sub(take, text), spans


def _unmask(text: str, spans: list[str]) -> str:
    """还原被保护的片段。"""
    return _PLACEHOLDER.sub(lambda match: spans[int(match.group(1))], text)


def _pick(ch: str, *, tone_only: bool, rng: random.Random) -> str:
    """按同声母韵母挑一个错字；无可选项时返回原字。"""
    base, tone = _read(ch)
    groups = _BY_BASE.get(base) or {}
    candidates = [
        item
        for group_tone, chars in groups.items()
        for item in chars
        if item != ch and (not tone_only or group_tone != tone)
    ]
    return rng.choice(candidates) if candidates else ch


def _replace_chars(
    text: str,
    *,
    error_rate: float,
    tone_rate: float,
    rng: random.Random,
    mistakes: list[str],
) -> str:
    """逐字注入同音或声调错误。"""
    chars: list[str] = []
    for ch in text:
        replacement = ch
        if error_rate and rng.random() < error_rate:
            replacement = _pick(ch, tone_only=False, rng=rng)
        elif tone_rate and rng.random() < tone_rate:
            replacement = _pick(ch, tone_only=True, rng=rng)
        if replacement != ch:
            mistakes.append(ch)
        chars.append(replacement)
    return "".join(chars)


def _replace_words(text: str, *, rate: float, rng: random.Random, mistakes: list[str]) -> str:
    """按词注入错写。"""
    if not rate:
        return text

    def take(match: re.Match[str]) -> str:
        if rng.random() >= rate:
            return match.group(0)
        mistakes.append(match.group(0))
        return _WORDS[match.group(0)]

    pattern = re.compile("|".join(re.escape(word) for word in _WORDS))
    return pattern.sub(take, text)


def _sentence_typo(text: str, config: dict[str, Any], rng: random.Random, mistakes: list[str]) -> str:
    """按句处理，标点原样保留。"""
    error_rate = _rate(config, "error_rate")
    tone_rate = _rate(config, "tone_error_rate")
    word_rate = _rate(config, "word_replace_rate")
    pieces = _SENTENCE.split(text)
    for index in range(0, len(pieces), 2):
        piece = _replace_words(pieces[index], rate=word_rate, rng=rng, mistakes=mistakes)
        pieces[index] = _replace_chars(
            piece, error_rate=error_rate, tone_rate=tone_rate, rng=rng, mistakes=mistakes
        )
    return "".join(pieces)


def _is_onebot(bot: Any) -> bool:
    """自纠只对 OneBot 平台启用。"""
    return bot is not None and "onebot" in platform_for_bot(bot).lower()


def apply_typo(
    text: str,
    *,
    bot: Any = None,
    rng: random.Random | None = None,
    config: dict[str, Any] | None = None,
) -> str:
    """返回发送文本；配置关闭、无文本或替换失败时原样返回。"""
    cfg = config if isinstance(config, dict) else typo_config()
    if not text or not cfg.get("enabled"):
        return text
    rng = rng or random.Random()
    masked, spans = _mask(text)
    mistakes: list[str] = []
    try:
        masked = _sentence_typo(masked, cfg, rng, mistakes)
    except Exception:
        return text
    result = _unmask(masked, spans)
    if (
        mistakes
        and cfg.get("correction_quote", True)
        and _is_onebot(bot)
        and rng.random() < _CORRECTION_CHANCE
    ):
        result += _CORRECTION.format(text=mistakes[0])
    return result
