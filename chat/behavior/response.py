"""回复断句、后处理与打字节奏。

断句与后处理流程直接搬运 MaiBot（commit 062d45c，`src/chat/utils/utils.py`），
只把全局配置和平台账号换成 FxBot 的 ConfigManager 与调用参数。
"""

from __future__ import annotations

import re
import json
import random
from typing import Any
from dataclasses import dataclass

from ...config import SYSTEM_DEFAULTS, get_manager as get_config_manager

_KAOMOJI = re.compile(
    r"("
    r"[(\[（【]"  # 左括号
    r"[^()\[\]（）【】]*?"
    r"[^一-龥a-zA-Z0-9\s]"  # 必须包含至少一个颜文字符号
    r"[^()\[\]（）【】]*?"
    r"[)\]）】]"
    r")"
    r"|"
    r"([▼▽・ᴥω･﹏^><≧≦￣｀´∀ヮДд︿﹀へ｡ﾟ╥╯╰︶︹•⁄]{2,15})"
)
_PARENTHESIZED = re.compile(r"[(\[（](?=.*[一-鿿]).*?[)\]）]")
_SEPARATORS = {"，", ",", " ", "。", ";", "\n"}
_QUOTES = {'"', "'", "“", "”", "‘", "’", "「", "」", "『", "』"}
_DASHES = {"-", "—"}


@dataclass
class ProcessedResponseSegment:
    """回复后处理产生的单条消息及其发送提示。"""

    text: str
    quote_previous: bool = False
    # 原始文本中紧跟本段的分隔符；多条消息被压缩合并为一条时，用它恢复句间的停顿
    separator: str = ""


def _defaults() -> tuple[dict[str, Any], dict[str, Any]]:
    chat = SYSTEM_DEFAULTS["chat"]
    return dict(chat["response_post_process"]), dict(chat["response_splitter"])


def response_config() -> tuple[dict[str, Any], dict[str, Any]]:
    """读取后处理与断句配置；读取失败时使用默认值。"""
    post, splitter = _defaults()
    try:
        chat = get_config_manager().get_system()["chat"]
    except Exception:
        return post, splitter
    for key, target in (("response_post_process", post), ("response_splitter", splitter)):
        value = chat.get(key)
        if isinstance(value, dict):
            target.update(value)
    return post, splitter


def is_english_letter(char: str) -> bool:
    """检查字符是否为英文字母（忽略大小写）。"""
    return "a" <= char.lower() <= "z"


def protect_kaomoji(sentence: str) -> tuple[str, dict[str, str]]:
    """把颜文字替换成占位符，避免断句时从中间拆开。"""
    placeholder_to_kaomoji: dict[str, str] = {}
    for match in _KAOMOJI.findall(sentence):
        kaomoji = match[0] or match[1]
        if kaomoji.startswith("[表情包") and kaomoji.endswith("]"):
            continue
        placeholder = f"__KAOMOJI_{len(placeholder_to_kaomoji)}__"
        sentence = sentence.replace(kaomoji, placeholder, 1)
        placeholder_to_kaomoji[placeholder] = kaomoji
    return sentence, placeholder_to_kaomoji


def recover_kaomoji(sentences: list[str], placeholder_to_kaomoji: dict[str, str]) -> list[str]:
    """恢复被保护的颜文字。"""
    recovered: list[str] = []
    for sentence in sentences:
        for placeholder, kaomoji in placeholder_to_kaomoji.items():
            sentence = sentence.replace(placeholder, kaomoji)
        recovered.append(sentence)
    return recovered


def get_western_ratio(paragraph: str) -> float:
    """计算段落中字母数字字符的西文比例。"""
    alnum_chars = [char for char in paragraph if char.isalnum()]
    if not alnum_chars:
        return 0.0
    western_count = sum(bool(is_english_letter(char)) for char in alnum_chars)
    return western_count / len(alnum_chars)


def _split_into_sentence_segments(text: str, rng: random.Random | None = None) -> list[tuple[str, str]]:
    """把文本切成 (句子, 尾随分隔符) 元组，并按长度概率合并。"""
    rng = rng or random
    text = re.sub(r"\n\s*\n+", "\n", text)
    text = re.sub(r"\n\s*([，,。;\s])", r"\n\1", text)
    text = re.sub(r"([，,。;\s])\s*\n", r"\1\n", text)

    len_text = len(text)
    if len_text < 3:
        if rng.random() < 0.01:
            return [(char, "") for char in text]
        return [(text, "")]

    inside_quote = [False] * len_text
    in_quote = False
    current_quote = ""
    for idx, char in enumerate(text):
        if char in _QUOTES:
            if not in_quote:
                in_quote = True
                current_quote = char
            elif char == current_quote or char in {'"', "'"} and current_quote in {'"', "'"}:
                in_quote = False
                current_quote = ""
        else:
            inside_quote[idx] = in_quote

    segments: list[tuple[str, str]] = []
    current = ""
    for idx, char in enumerate(text):
        if char not in _SEPARATORS:
            current += char
            continue
        if inside_quote[idx]:
            can_split = False
        elif char == "\n":
            can_split = True
        else:
            can_split = True
            if idx > 0 and text[idx - 1] in {":", "："}:
                can_split = False
            if idx < len_text - 1 and text[idx + 1] in {":", "："}:
                can_split = False
            if can_split and char == " " and 0 < idx < len_text - 1:
                prev_char, next_char = text[idx - 1], text[idx + 1]
                if prev_char in _DASHES or next_char in _DASHES:
                    can_split = False
                elif (prev_char.isdigit() or is_english_letter(prev_char)) and (
                    next_char.isdigit() or is_english_letter(next_char)
                ):
                    can_split = False
        if can_split:
            if current:
                segments.append((current, char))
            elif char in {" ", "\n"}:
                segments.append(("", char))
            current = ""
        else:
            current += char
    if current:
        segments.append((current, ""))

    segments = [(content, sep) for content, sep in segments if content or sep]
    if not segments:
        return [(text, "")] if text else []

    if len_text < 12:
        split_strength = 0.2
    elif len_text < 32:
        split_strength = 0.6
    else:
        split_strength = 0.7
    merge_probability = 1.0 - split_strength

    merged: list[tuple[str, str]] = []
    idx = 0
    while idx < len(segments):
        content, sep = segments[idx]
        if idx + 1 < len(segments) and content and sep != "\n" and rng.random() < merge_probability:
            next_content, next_sep = segments[idx + 1]
            if next_content:
                merged.append((content + sep + next_content, next_sep))
            else:
                merged.append((content, next_sep))
            idx += 2
        else:
            merged.append((content, sep))
            idx += 1

    sentence_segments = [(content, sep) for content, sep in merged if content]
    sentence_segments = [(sentence, sep) for sentence, sep in sentence_segments if sentence.strip()]
    normalized: list[tuple[str, str]] = []
    for sentence, sep in sentence_segments:
        value = re.sub(r"[^\S\r\n]*[\r\n]+[^\S\r\n]*", " ", sentence).strip()
        if value:
            normalized.append((value, sep))
    return normalized


def _merge_segments_to_max_count(
    segments: list[ProcessedResponseSegment], max_count: int
) -> list[ProcessedResponseSegment]:
    """压缩回复段数量，并优先让需要引用的纠正内容保持在消息开头。"""
    if len(segments) <= max_count:
        return segments
    if max_count <= 0:
        return []

    segment_count = len(segments)
    required_starts = [i for i, segment in enumerate(segments) if i > 0 and segment.quote_previous]
    group_starts = {0, *required_starts[: max_count - 1]}

    evenly_spaced_starts: list[int] = []
    start_index = 0
    for group_index in range(max_count):
        remaining_segments = segment_count - start_index
        remaining_groups = max_count - group_index
        group_size = (remaining_segments + remaining_groups - 1) // remaining_groups
        evenly_spaced_starts.append(start_index)
        start_index += group_size
    for candidate_start in evenly_spaced_starts:
        if len(group_starts) >= max_count:
            break
        group_starts.add(candidate_start)

    sorted_starts = sorted(group_starts)
    merged: list[ProcessedResponseSegment] = []
    for group_index, group_start in enumerate(sorted_starts):
        group_end = sorted_starts[group_index + 1] if group_index + 1 < len(sorted_starts) else segment_count
        group = segments[group_start:group_end]
        text_parts: list[str] = []
        for position, segment in enumerate(group):
            text_parts.append(segment.text)
            if position < len(group) - 1:
                text_parts.append(segment.separator)
        merged.append(
            ProcessedResponseSegment(
                text="".join(text_parts),
                quote_previous=group[0].quote_previous,
                separator=group[-1].separator,
            )
        )
    return merged


def default_reply(nickname: str = "", rng: random.Random | None = None) -> str:
    """回复不可用时使用的兜底句，取自 MaiBot。"""
    rng = rng or random
    return rng.choice(
        [
            f"{nickname}不知道哦" if nickname else "不知道哦",
            f"{nickname}不知道" if nickname else "不知道",
            "不知道哦",
            "不知道",
            "不晓得",
            "懒得说",
            "()",
        ]
    )


def process_response(
    text: str,
    *,
    nickname: str = "",
    post: dict[str, Any] | None = None,
    splitter: dict[str, Any] | None = None,
    rng: random.Random | None = None,
) -> list[ProcessedResponseSegment]:
    """按 MaiBot 的后处理流程拆成待发送分段。"""
    rng = rng or random
    default_post, default_splitter = response_config()
    post_cfg = {**default_post, **(post or {})}
    splitter_cfg = {**default_splitter, **(splitter or {})}

    if not post_cfg.get("enable_response_post_process", True):
        return [ProcessedResponseSegment(text)]

    if splitter_cfg.get("enable_kaomoji_protection"):
        protected_text, kaomoji_mapping = protect_kaomoji(text)
    else:
        protected_text, kaomoji_mapping = text, {}

    # 括号里的动作或旁白不进入发送文本；整句只有括号内容时按空回复处理
    cleaned_text = _PARENTHESIZED.sub("", protected_text)
    if not cleaned_text.strip():
        return [ProcessedResponseSegment("呃呃")]

    max_length = int(splitter_cfg.get("max_length", 512)) * 2
    max_sentence_num = int(splitter_cfg.get("max_sentence_num", 8))
    max_split_num = int(splitter_cfg.get("max_split_num", 3))

    value = cleaned_text.strip()
    if get_western_ratio(value) < 0.1 and len(value) > max_length:
        return [ProcessedResponseSegment(recover_kaomoji([value], kaomoji_mapping)[0])]

    if splitter_cfg.get("enable", True):
        split_sentences = _split_into_sentence_segments(value, rng)
    else:
        split_sentences = [(value, "")]

    segments = [ProcessedResponseSegment(sentence, separator=sep) for sentence, sep in split_sentences]
    if len(segments) > max_sentence_num:
        if splitter_cfg.get("enable_overflow_return_all"):
            segments = [ProcessedResponseSegment(value)]
        # 超限只压缩交付段数，不能用随机闲聊句替换真实结果。

    segments = _merge_segments_to_max_count(segments, max_split_num)
    if splitter_cfg.get("enable_kaomoji_protection"):
        recovered = recover_kaomoji([segment.text for segment in segments], kaomoji_mapping)
        segments = [
            ProcessedResponseSegment(
                text=recovered_text,
                quote_previous=segment.quote_previous,
                separator=segment.separator,
            )
            for segment, recovered_text in zip(segments, recovered, strict=True)
        ]
    return segments


_SPLITTER_PROMPT = """
请不要思考，快速地把下面的回复进行断句，按自然语义拆成若干条适合逐条发送的消息。
要求：
1. 只能返回 JSON 字符串数组，例如 ["第一句。", "第二句？"]，不要 Markdown、解释或代码块。
2. 只能在原文已有字符之间选择切分位置，不能改写文本、增删或调整顺序。
3. 不要把括号、引号、颜文字或代码从中间拆开。如果是单独的句子，不要在在结尾留逗号。
4. 如果是完整代码，公式，或者整理阅读更合适的文段，不需要拆分，返回"no_split"。
5. 如果是本身比较短，或者表意清晰的句子，不需要拆分，返回"no_split"。

原文：
"""

_NO_SPLIT_MARKER = "no_split"

_PUNCTUATION_TABLE = {
    **{code: code - 0xFEE0 for code in range(0xFF01, 0xFF5F)},
    0x3000: 0x20,
    0x3002: ord("."),
    0x3001: ord(","),
    0x2018: ord("'"),
    0x2019: ord("'"),
    0x201C: ord('"'),
    0x201D: ord('"'),
    0x3010: ord("["),
    0x3011: ord("]"),
    0x300A: ord("<"),
    0x300B: ord(">"),
}


def _strip_whitespace(text: str) -> str:
    """去除全部空白，并把中英文标点归一，用于宽松比较。"""
    return "".join(text.split()).translate(_PUNCTUATION_TABLE)


def _map_sentences_to_original(text: str, sentences: list[str]) -> list[str]:
    """按各句的非空白字符数把切分位置映射回原文，丢弃句间空白。"""
    counts = [len(_strip_whitespace(sentence)) for sentence in sentences]
    counts = [count for count in counts if count > 0]
    result: list[str] = []
    start = 0
    consumed = 0
    target = 0
    for index, count in enumerate(counts):
        target += count
        if index == len(counts) - 1:
            result.append(text[start:])
            break
        end = start
        while consumed < target:
            if not text[end].isspace():
                consumed += 1
            end += 1
        sentence_end = end
        while end < len(text) and text[end].isspace():
            end += 1
        # 段尾逗号后的空白要保留，供分隔符还原；其他句间空白在逐条发送时无意义
        result.append(text[start : end if text[sentence_end - 1] in "，," else sentence_end])
        start = end
    return result


def _extract_trailing_comma(sentence: str) -> tuple[str, str]:
    """将段尾逗号移入分隔符，逐条发送时省略，压缩合并时恢复。"""
    content = sentence.rstrip("，, \t\r\n")
    separator = sentence[len(content) :]
    if content.strip() and ("，" in separator or "," in separator):
        return content, separator
    return sentence, ""


async def split_text_with_llm(text: str, provider: Any) -> list[tuple[str, str]]:
    """调用当前回复模型按语义断句。"""
    if not text:
        return []
    response = await provider.chat(messages=[{"role": "user", "content": _SPLITTER_PROMPT + text}])
    payload = str(getattr(response, "content", "") or "").strip()
    if payload.startswith("```json") and payload.endswith("```"):
        payload = payload[7:-3].strip()
    elif payload.startswith("```") and payload.endswith("```"):
        payload = payload[3:-3].strip()

    if payload.strip('"').strip() == _NO_SPLIT_MARKER:
        return [_extract_trailing_comma(text)]

    try:
        sentences = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError("LLM 断句结果不是合法 JSON 数组") from exc
    if not isinstance(sentences, list) or not sentences or not all(isinstance(item, str) for item in sentences):
        raise ValueError("LLM 断句结果必须是非空字符串数组")
    if sentences == [_NO_SPLIT_MARKER]:
        return [_extract_trailing_comma(text)]

    if _strip_whitespace("".join(sentences)) != _strip_whitespace(text):
        raise ValueError("LLM 断句结果未完整保留原文")
    return [_extract_trailing_comma(sentence) for sentence in _map_sentences_to_original(text, sentences)]


async def process_response_async(
    text: str,
    *,
    provider: Any,
    nickname: str = "",
    post: dict[str, Any] | None = None,
    splitter: dict[str, Any] | None = None,
    rng: random.Random | None = None,
) -> list[ProcessedResponseSegment]:
    """按配置选择规则断句或 LLM 断句。"""
    rng = rng or random
    default_post, default_splitter = response_config()
    post_cfg = {**default_post, **(post or {})}
    splitter_cfg = {**default_splitter, **(splitter or {})}
    if (
        splitter_cfg.get("mode") != "llm"
        or not post_cfg.get("enable_response_post_process", True)
        or not splitter_cfg.get("enable", True)
    ):
        return process_response(text, nickname=nickname, post=post_cfg, splitter=splitter_cfg, rng=rng)

    try:
        sentences = await split_text_with_llm(text, provider)
    except Exception:
        sentences = [(text, "")]

    segments = [ProcessedResponseSegment(sentence, separator=sep) for sentence, sep in sentences]
    if len(segments) > int(splitter_cfg.get("max_sentence_num", 8)):
        if splitter_cfg.get("enable_overflow_return_all"):
            return [ProcessedResponseSegment(text)]
    return _merge_segments_to_max_count(segments, int(splitter_cfg.get("max_split_num", 3)))


def typing_time(text: str, *, is_emoji: bool = False, is_image: bool = False) -> float:
    """按 MaiBot `calculate_typing_time` 估算输入耗时。"""
    post, _ = response_config()
    try:
        speed = float(post.get("typing_speed", 1.0))
    except (TypeError, ValueError):
        speed = 1.0
    if speed <= 0:
        return 0.0
    chinese_time, english_time = 0.3, 0.15
    chinese_chars = sum("一" <= char <= "鿿" for char in text)
    if not (is_emoji or is_image) and chinese_chars == 1 and len(text.strip()) == 1:
        return chinese_time * 3 + 0.3

    total = 0.0
    for char in text:
        total += chinese_time if "一" <= char <= "鿿" else english_time
    if is_emoji or is_image:
        total = 1.0
    return total * speed
