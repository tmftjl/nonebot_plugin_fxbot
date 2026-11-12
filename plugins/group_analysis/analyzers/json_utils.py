"""JSON 解析、修复与正则降级，逻辑取自原版插件。"""

from __future__ import annotations

import re
import json
from typing import Any

from nonebot import logger


def fix_json(text: str) -> str:
    """修复中文符号、代码块包裹、尾逗号和截断等问题。"""
    try:
        text = re.sub(r"```json\s*", "", text)
        text = re.sub(r"```\s*$", "", text)
        text = text.replace("\n", " ").replace("\r", " ")
        text = re.sub(r"\s+", " ", text)
        text = text.replace("“", '"').replace("”", '"')
        text = text.replace("‘", "'").replace("’", "'")
        text = text.replace("，", ",")
        text = text.replace("：", ":")
        text = text.replace("（", "(").replace("）", ")")
        text = text.replace("【", "[").replace("】", "]")

        def escape_quotes_in_strings(match: re.Match[str]) -> str:
            content = match.group(1).replace('"', '\\"')
            return f'"{content}"'

        text = re.sub(r'"([^"]*(?:"[^"]*)*)"', escape_quotes_in_strings, text)

        if not text.endswith("]"):
            last_complete = text.rfind("}")
            if last_complete > 0:
                text = text[: last_complete + 1] + "]"

        text = re.sub(r"}\s*{", "}, {", text)

        def quote_field_names(match: re.Match[str]) -> str:
            prefix = match.group(1)
            key = match.group(2)
            return f'{prefix}"{key}":'

        text = re.sub(r"([{,]\s*)([a-zA-Z_][a-zA-Z0-9_]*)\s*:", quote_field_names, text)
        text = re.sub(r",\s*}", "}", text)
        text = re.sub(r",\s*]", "]", text)
        return text.strip()
    except Exception as exc:
        logger.error(f"JSON修复失败: {exc}")
        return text


def _parse_json_with_pattern(
    result_text: str, pattern: str, data_type: str, expected_type_name: str = "数据"
) -> tuple[bool, object, str | None]:
    try:
        clean_text = result_text.strip()
        clean_text = re.sub(r"```(?:json)?\s*", "", clean_text)
        clean_text = re.sub(r"```\s*$", "", clean_text)
        json_match = re.search(pattern, clean_text, re.DOTALL)
        if not json_match:
            return False, None, f"{data_type}响应中未找到JSON{expected_type_name}"

        json_text = json_match.group()
        try:
            return True, json.loads(json_text), None
        except json.JSONDecodeError:
            logger.debug(f"{data_type}直接解析失败，尝试修复JSON...")

        fixed_match = re.search(pattern, fix_json(json_text), re.DOTALL)
        if fixed_match:
            try:
                return True, json.loads(fixed_match.group()), None
            except json.JSONDecodeError as exc:
                return False, None, f"{data_type}JSON修复后解析仍失败: {exc}"
        return False, None, f"{data_type}修复后未找到JSON{expected_type_name}"
    except Exception as exc:
        return False, None, f"{data_type}解析异常: {exc}"


def _is_object_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, dict) for item in value)


def parse_json_response(result_text: str, data_type: str) -> tuple[bool, list[dict[str, Any]] | None, str | None]:
    """解析 JSON 数组响应。"""
    success, data, err = _parse_json_with_pattern(result_text, r"\[.*\]", data_type, "数组")
    if success and _is_object_list(data):
        return True, list(data), None
    return False, None, err


def parse_json_object_response(result_text: str, data_type: str) -> tuple[bool, dict[str, Any] | None, str | None]:
    """解析 JSON 对象响应。"""
    success, data, err = _parse_json_with_pattern(result_text, r"\{.*\}", data_type, "对象")
    if success and isinstance(data, dict):
        return True, data, None
    return False, None, err


def _clean_json_string(text: str) -> str:
    return text.replace('\\"', '"').replace("\\n", " ").replace("\\t", " ")


def extract_topics_with_regex(result_text: str, max_topics: int) -> list[dict[str, Any]]:
    try:
        pattern = (
            r'\{\s*"topic":\s*"([^"]*(?:\\.[^"]*)*)"\s*,\s*"contributors":\s*\[(.*?)\],?\s*'
            r'"detail":\s*"([^"]*(?:\\.[^"]*)*)"\s*\}'
        )
        matches = re.findall(pattern, result_text, re.DOTALL)
        if not matches:
            pattern = (
                r'"topic":\s*"([^"]*(?:\\.[^"]*)*)"[^}]*"contributors":\s*\[(.*?)\]'
                r'[^}]*"detail":\s*"([^"]*(?:\\.[^"]*)*)"'
            )
            matches = re.findall(pattern, result_text, re.DOTALL)

        topics: list[dict[str, Any]] = []
        for match in matches[:max_topics]:
            contributors = [item.strip() for item in re.findall(r'"([^"]+)"', match[1])] or ["群友"]
            topics.append(
                {
                    "topic": match[0].strip(),
                    "contributors": contributors[:5],
                    "detail": _clean_json_string(match[2].strip()),
                }
            )
        return topics
    except Exception as exc:
        logger.error(f"话题正则表达式提取失败: {exc}")
        return []


def extract_user_titles_with_regex(result_text: str, max_count: int) -> list[dict[str, Any]]:
    try:
        pattern = (
            r'\{\s*"name":\s*"([^"]*(?:\\.[^"]*)*)"\s*,\s*"user_id":\s*"([^"]+)"\s*,\s*'
            r'"title":\s*"([^"]*(?:\\.[^"]*)*)"\s*,\s*"mbti":\s*"([^"]+)"\s*,\s*'
            r'"reason":\s*"([^"]*(?:\\.[^"]*)*)"\s*\}'
        )
        matches = re.findall(pattern, result_text, re.DOTALL)
        if not matches:
            pattern = (
                r'"name":\s*"([^"]*(?:\\.[^"]*)*)"[^}]*"user_id":\s*"([^"]+)"[^}]*'
                r'"title":\s*"([^"]*(?:\\.[^"]*)*)"[^}]*"mbti":\s*"([^"]+)"[^}]*'
                r'"reason":\s*"([^"]*(?:\\.[^"]*)*)"'
            )
            matches = re.findall(pattern, result_text, re.DOTALL)

        return [
            {
                "name": match[0].strip(),
                "user_id": match[1].strip(),
                "title": match[2].strip(),
                "mbti": match[3].strip(),
                "reason": _clean_json_string(match[4].strip()),
            }
            for match in matches[:max_count]
        ]
    except Exception as exc:
        logger.error(f"用户称号正则表达式提取失败: {exc}")
        return []


def extract_golden_quotes_with_regex(result_text: str, max_count: int) -> list[dict[str, Any]]:
    try:
        pattern = (
            r'\{\s*"content":\s*"([^"]*(?:\\.[^"]*)*)"\s*,\s*"sender":\s*"([^"]*(?:\\.[^"]*)*)"\s*,\s*'
            r'"reason":\s*"([^"]*(?:\\.[^"]*)*)"\s*\}'
        )
        matches = re.findall(pattern, result_text, re.DOTALL)
        if not matches:
            pattern = (
                r'"content":\s*"([^"]*(?:\\.[^"]*)*)"[^}]*"sender":\s*"([^"]*(?:\\.[^"]*)*)"[^}]*'
                r'"reason":\s*"([^"]*(?:\\.[^"]*)*)"'
            )
            matches = re.findall(pattern, result_text, re.DOTALL)

        return [
            {
                "content": _clean_json_string(match[0].strip()),
                "sender": match[1].strip(),
                "reason": _clean_json_string(match[2].strip()),
            }
            for match in matches[:max_count]
        ]
    except Exception as exc:
        logger.error(f"金句正则表达式提取失败: {exc}")
        return []


def extract_quality_with_regex(result_text: str) -> dict[str, Any] | None:
    try:
        title_m = re.search(r'"title"\s*:\s*"([^"]*(?:\\.[^"]*)*)"', result_text)
        subtitle_m = re.search(r'"subtitle"\s*:\s*"([^"]*(?:\\.[^"]*)*)"', result_text)
        summary_m = re.search(r'"summary"\s*:\s*"([^"]*(?:\\.[^"]*)*)"', result_text)
        dims_match = re.search(r'"dimensions"\s*:\s*\[(.*)\]', result_text, re.DOTALL)
        dims: list[dict[str, Any]] = []
        if dims_match:
            for name, percentage, comment in re.findall(
                r'\{[^}]*"name"\s*:\s*"([^"]*)"[^}]*'
                r'"percentage"\s*:\s*([\d.]+)[^}]*'
                r'"comment"\s*:\s*"([^"]*(?:\\.[^"]*)*)"[^}]*\}',
                dims_match.group(1),
            ):
                dims.append({"name": name, "percentage": float(percentage), "comment": comment})
        if not dims:
            return None
        return {
            "title": title_m.group(1) if title_m else "聊天质量锐评",
            "subtitle": subtitle_m.group(1) if subtitle_m else "今天的群里发生了什么？",
            "dimensions": dims,
            "summary": summary_m.group(1) if summary_m else "今天也是充满活力的一天。",
        }
    except Exception as exc:
        logger.error(f"聊天质量正则表达式提取失败: {exc}")
        return None
