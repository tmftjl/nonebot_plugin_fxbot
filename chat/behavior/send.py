"""回复拆分与分段发送节奏。"""

from __future__ import annotations

import re

TARGET_CHARS = 120
PLATFORM_MAX_CHARS = 1000
MAX_PARTS = 3

# URL、行内代码和围栏代码块不参与断句
_PROTECTED = re.compile(r"```.*?(?:```|$)|`[^`\n]+`|https?://[^\s<>\"'，。！？；、）】」]+", re.S)
_SENTENCE = re.compile(r"(?<=[。！？!?\n])")
_SOFT = re.compile(r"(?<=[，,、；;：:])|(?<=\s)")


def _split_plain(text: str) -> list[str]:
    """先按句末标点切，过长句子再按逗号或空格切。"""
    units: list[str] = []
    for sentence in _SENTENCE.split(text):
        if not sentence:
            continue
        units.extend(part for part in (_SOFT.split(sentence) if len(sentence) > TARGET_CHARS else [sentence]) if part)
    return units


def _units(text: str) -> list[str]:
    """把文本切成不可再分的片段，保护 URL 与代码块。"""
    units: list[str] = []
    position = 0
    for match in _PROTECTED.finditer(text):
        units.extend(_split_plain(text[position : match.start()]))
        units.append(match.group(0))
        position = match.end()
    units.extend(_split_plain(text[position:]))
    return units


def split_reply(
    text: str,
    *,
    target: int = TARGET_CHARS,
    max_parts: int = MAX_PARTS,
    platform_max: int = PLATFORM_MAX_CHARS,
) -> list[str]:
    """按目标段长装箱，最多 max_parts 段；剩余并入末段，再受平台硬上限约束。"""
    content = str(text or "").strip()
    if not content:
        return []
    parts: list[str] = []
    current = ""
    for unit in _units(content):
        if current and len(current) + len(unit) > target:
            parts.append(current)
            current = unit
        else:
            current += unit
    if current:
        parts.append(current)
    if len(parts) > max_parts:
        parts = parts[: max_parts - 1] + ["".join(parts[max_parts - 1 :])]
    result: list[str] = []
    for part in parts:
        # 目标段长可以超出，平台硬上限不能；超出时宁可多发一条也不截断
        while len(part) > platform_max:
            result.append(part[:platform_max])
            part = part[platform_max:]
        if part:
            result.append(part)
    return result


def part_delay(text: str, *, is_group: bool = True) -> float:
    """后续分段的间隔：按长度线性增长，群聊封顶 2 秒，私聊减半。"""
    delay = min(0.6 + 0.005 * len(text), 2.0)
    return delay if is_group else delay / 2
