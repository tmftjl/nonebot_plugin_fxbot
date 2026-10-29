"""人工风格存储使用的结构化记录。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StyleExample:
    """一条逐字摘自原始消息的风格示例。"""

    text: str
    seq: int


@dataclass(frozen=True)
class StyleJargon:
    """一个群内特有词；低置信释义标记为 rejected。"""

    term: str
    normalized: str
    meaning: str
    status: str


@dataclass(frozen=True)
class StyleResult:
    """风格及其来源的结构化记录。"""

    rules: tuple[str, ...]
    examples: tuple[StyleExample, ...]
    summary: str
    jargon: tuple[StyleJargon, ...]
