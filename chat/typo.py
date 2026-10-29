"""发送前错字模拟；历史仍保存干净文本。"""

from __future__ import annotations

import re
import random
from typing import Any
from functools import lru_cache
from dataclasses import dataclass

from pypinyin import Style, pinyin

from ..config import get_manager as get_config_manager

# 同音字库：同一拼音的不同声调、不同字
_POOL = (
    "的得在再是时使诗事市式妈麻马骂衣移椅意花滑画话华书熟属树数"
    "冤元远愿风逢讽凤张章掌丈长常场唱见件间简建想相向香有又右友"
    "好号毫爱哀挨看砍说硕多夺天甜填起气七来赖去取会回灰就旧酒"
    "没每美认任过国们门生声胜小笑校新心信明名命点电店里理力李"
    "上商伤下夏大打达中种重难南男了乐着这业夜还海孩最嘴罪很狠恨"
    "不布步要药摇吃迟尺完玩晚之只指做作坐到道倒"
)

# 字频：jieba dict.txt 归一化到 0-1000，与 MaiBot 的 min_freq 同尺度
_FREQ = {
    "的": 296.09,
    "得": 228.34,
    "在": 678.01,
    "再": 67.15,
    "是": 832.0,
    "时": 299.84,
    "使": 102.53,
    "诗": 16.02,
    "事": 171.26,
    "市": 198.44,
    "式": 65.22,
    "妈": 15.06,
    "麻": 12.33,
    "马": 76.49,
    "骂": 7.98,
    "衣": 19.67,
    "移": 16.04,
    "椅": 3.24,
    "意": 105.16,
    "花": 57.95,
    "滑": 7.11,
    "画": 21.7,
    "话": 73.41,
    "华": 71.44,
    "书": 71.7,
    "熟": 13.57,
    "属": 44.8,
    "树": 22.89,
    "数": 99.39,
    "冤": 2.43,
    "元": 92.68,
    "远": 40.3,
    "愿": 21.37,
    "风": 64.22,
    "逢": 2.95,
    "讽": 1.27,
    "凤": 7.37,
    "张": 69.81,
    "章": 26.51,
    "掌": 21.87,
    "丈": 8.4,
    "长": 225.94,
    "常": 108.78,
    "场": 103.89,
    "唱": 8.35,
    "见": 118.7,
    "件": 58.04,
    "间": 121.26,
    "简": 20.27,
    "建": 129.25,
    "想": 113.39,
    "相": 116.05,
    "向": 101.33,
    "香": 23.38,
    "有": 643.71,
    "又": 128.97,
    "右": 26.89,
    "友": 20.9,
    "好": 145.51,
    "号": 58.22,
    "毫": 14.37,
    "爱": 36.84,
    "哀": 2.67,
    "挨": 2.54,
    "看": 120.62,
    "砍": 3.53,
    "说": 274.82,
    "硕": 2.51,
    "多": 208.32,
    "夺": 11.95,
    "天": 219.32,
    "甜": 4.59,
    "填": 4.54,
    "起": 155.34,
    "气": 87.08,
    "七": 26.88,
    "来": 383.77,
    "赖": 4.9,
    "去": 167.88,
    "取": 60.22,
    "会": 368.91,
    "回": 88.95,
    "灰": 8.68,
    "就": 249.04,
    "旧": 14.61,
    "酒": 24.22,
    "没": 126.43,
    "每": 52.63,
    "美": 90.76,
    "认": 51.86,
    "任": 94.03,
    "过": 222.52,
    "国": 582.05,
    "们": 251.73,
    "门": 177.66,
    "生": 295.08,
    "声": 83.09,
    "胜": 29.24,
    "小": 166.11,
    "笑": 49.29,
    "校": 46.0,
    "新": 138.24,
    "心": 167.4,
    "信": 63.87,
    "明": 130.9,
    "名": 124.36,
    "命": 72.63,
    "点": 107.53,
    "电": 85.42,
    "店": 13.03,
    "里": 190.09,
    "理": 149.51,
    "力": 146.26,
    "李": 49.81,
    "上": 431.58,
    "商": 52.37,
    "伤": 25.02,
    "下": 253.37,
    "夏": 21.92,
    "大": 616.01,
    "打": 71.87,
    "达": 67.52,
    "中": 607.07,
    "种": 153.9,
    "重": 139.18,
    "难": 54.37,
    "南": 127.84,
    "男": 22.19,
    "了": 754.04,
    "乐": 35.42,
    "着": 206.15,
    "这": 460.31,
    "业": 195.35,
    "夜": 20.8,
    "还": 158.7,
    "海": 107.14,
    "孩": 21.46,
    "最": 122.7,
    "嘴": 13.14,
    "罪": 14.02,
    "很": 80.43,
    "狠": 4.47,
    "恨": 6.69,
    "不": 751.28,
    "布": 62.68,
    "步": 52.13,
    "要": 298.94,
    "药": 34.33,
    "摇": 11.2,
    "吃": 41.91,
    "迟": 5.67,
    "尺": 6.3,
    "完": 55.08,
    "玩": 8.66,
    "晚": 21.08,
    "之": 235.96,
    "只": 134.73,
    "指": 61.06,
    "做": 60.58,
    "作": 216.75,
    "坐": 26.22,
    "到": 315.62,
    "道": 272.24,
    "倒": 29.95,
}

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


@dataclass(frozen=True)
class TypoText:
    """一条发送文本及需要补发的纠正内容；correction 为空表示不发。"""

    text: str
    correction: str = ""


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


def _pick(ch: str, *, tone_only: bool, rng: random.Random, min_freq: float = 0.0) -> str:
    """按同声母韵母挑一个错字；候选字频低于 min_freq 时跳过。"""
    base, tone = _read(ch)
    groups = _BY_BASE.get(base) or {}
    candidates = [
        item
        for group_tone, chars in groups.items()
        for item in chars
        if item != ch and (not tone_only or group_tone != tone) and _FREQ.get(item, 0.0) >= min_freq
    ]
    return rng.choice(candidates) if candidates else ch


def _min_freq(config: dict[str, Any]) -> float:
    value = config.get("min_freq")
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def _replace_chars(
    text: str,
    *,
    error_rate: float,
    tone_rate: float,
    rng: random.Random,
    mistakes: list[str],
    min_freq: float = 0.0,
) -> str:
    """逐字注入同音或声调错误。"""
    chars: list[str] = []
    for ch in text:
        replacement = ch
        if error_rate and rng.random() < error_rate:
            replacement = _pick(ch, tone_only=False, rng=rng, min_freq=min_freq)
        elif tone_rate and rng.random() < tone_rate:
            replacement = _pick(ch, tone_only=True, rng=rng, min_freq=min_freq)
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


def _sentence_typo(text: str, config: dict[str, Any], rng: random.Random, words: list[str], chars: list[str]) -> str:
    """按句处理，标点原样保留；分别记下词级与字级错写的原词。"""
    error_rate = _rate(config, "error_rate")
    tone_rate = _rate(config, "tone_error_rate")
    word_rate = _rate(config, "word_replace_rate")
    min_freq = _min_freq(config)
    pieces = _SENTENCE.split(text)
    for index in range(0, len(pieces), 2):
        piece = _replace_words(pieces[index], rate=word_rate, rng=rng, mistakes=words)
        pieces[index] = _replace_chars(
            piece,
            error_rate=error_rate,
            tone_rate=tone_rate,
            rng=rng,
            mistakes=chars,
            min_freq=min_freq,
        )
    return "".join(pieces)


def _correction(words: list[str], chars: list[str], rng: random.Random) -> str:
    """按 MaiBot 规则挑一条纠正：50% 给出，整词错写优先于单字。"""
    choices = words or chars
    return rng.choice(choices) if choices and rng.random() < 0.5 else ""


def apply_typo(
    text: str,
    *,
    rng: random.Random | None = None,
    config: dict[str, Any] | None = None,
) -> TypoText:
    """生成错字句与纠正内容；一半概率放弃错字直接发原句。"""
    cfg = config if isinstance(config, dict) else typo_config()
    if not text or not cfg.get("enable"):
        return TypoText(text)
    rng = rng or random.Random()
    masked, spans = _mask(text)
    words: list[str] = []
    chars: list[str] = []
    try:
        masked = _sentence_typo(masked, cfg, rng, words, chars)
    except Exception:
        return TypoText(text)
    typoed = _unmask(masked, spans)
    correction = _correction(words, chars, rng)
    if not correction:
        return TypoText(typoed)
    return TypoText(typoed, correction) if rng.random() < 0.5 else TypoText(text)
