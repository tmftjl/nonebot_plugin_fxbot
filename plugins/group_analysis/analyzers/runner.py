"""四路分析并发执行，编排方式与原版 analyze_all_concurrent 一致。"""

from __future__ import annotations

import asyncio
from typing import Any

from nonebot import logger

from .quote import GoldenQuoteAnalyzer
from .title import UserTitleAnalyzer
from .topic import TopicAnalyzer
from .quality import ChatQualityAnalyzer
from ....chat.providers.routing import fallback_provider

DEFAULT_FEATURES: dict[str, Any] = {
    "topic_analysis_enabled": True,
    "user_title_analysis_enabled": True,
    "golden_quote_analysis_enabled": True,
    "chat_quality_analysis_enabled": True,
    "max_topics": 5,
    "max_user_titles": 8,
    "max_golden_quotes": 5,
}


def empty_analysis() -> dict[str, Any]:
    return {
        "topics": [],
        "titles": [],
        "quotes": [],
        "quality": {"title": "", "subtitle": "", "dimensions": [], "summary": ""},
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def _usage_of(usage: dict[str, int] | None) -> dict[str, int]:
    return {key: int((usage or {}).get(key) or 0) for key in ("prompt_tokens", "completion_tokens", "total_tokens")}


def _topics_for_render(topics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "title": topic["topic"],
            "summary": topic["detail"],
            "keywords": [],
            "participants": topic["contributors"],
            "contributor_ids": topic["contributor_ids"],
        }
        for topic in topics
    ]


async def analyze(
    group_id: str,
    messages: list[dict[str, Any]],
    *,
    users: list[dict[str, Any]],
    features: dict[str, Any] | None = None,
    persona: str = "",
) -> dict[str, Any]:
    """按启用的模块并发调用模型；单模块失败只影响自身。"""
    flags = {**DEFAULT_FEATURES, **(features or {})}
    topic_enabled = bool(flags["topic_analysis_enabled"])
    title_enabled = bool(flags["user_title_analysis_enabled"]) and bool(users)
    quote_enabled = bool(flags["golden_quote_analysis_enabled"])
    quality_enabled = bool(flags["chat_quality_analysis_enabled"])
    if not (topic_enabled or title_enabled or quote_enabled or quality_enabled):
        return empty_analysis()

    quote_messages = [msg for msg in messages if 2 <= len(msg["content"]) <= 500]
    usage: dict[str, int] = {}
    try:
        async with fallback_provider("utils") as model:
            results = await asyncio.gather(
                TopicAnalyzer(model, int(flags["max_topics"]), persona).analyze(messages)
                if topic_enabled
                else _skipped([]),
                UserTitleAnalyzer(model, int(flags["max_user_titles"]), persona).analyze(users)
                if title_enabled
                else _skipped([]),
                GoldenQuoteAnalyzer(model, int(flags["max_golden_quotes"]), persona).analyze(quote_messages)
                if quote_enabled
                else _skipped([]),
                ChatQualityAnalyzer(model, 8, persona).analyze(messages) if quality_enabled else _skipped(None),
                return_exceptions=True,
            )
            usage = model.usage.to_dict()
    except Exception:
        logger.opt(exception=True).warning(f"[group_analysis] 群 {group_id} 日报模型调用失败")
        return empty_analysis()

    result = empty_analysis()
    result["usage"] = _usage_of(usage)
    for name, value in zip(("topic", "user_title", "golden_quote", "chat_quality"), results, strict=True):
        if isinstance(value, BaseException):
            logger.error(f"[group_analysis] 群 {group_id} 分析任务 {name} 失败: {value}")
    topic_result, title_result, quote_result, quality_result = results
    if not isinstance(topic_result, BaseException):
        result["topics"] = _topics_for_render(topic_result)
    if not isinstance(title_result, BaseException):
        result["titles"] = title_result
    if not isinstance(quote_result, BaseException):
        result["quotes"] = quote_result
    if not isinstance(quality_result, BaseException) and quality_result:
        result["quality"] = quality_result
    logger.info(
        f"[group_analysis] 群 {group_id} 并发分析完成 - 话题: {len(result['topics'])}, "
        f"称号: {len(result['titles'])}, 金句: {len(result['quotes'])}, "
        f"质量锐评: {1 if result['quality']['dimensions'] else 0}"
    )
    return result


async def _skipped(value: Any) -> Any:
    return value
