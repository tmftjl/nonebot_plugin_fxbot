"""情绪、熟悉度与行为提示；只提供给 Planner，不改变门控公式。"""

from __future__ import annotations

import json
from datetime import datetime

from nonebot import logger

from .types import ChatRequest
from .memory.models import ChatState, ChatBehavior
from .memory.repository import mood_enabled, chat_repository


def scene_tags(request: ChatRequest) -> list[str]:
    """把本轮消息压成稳定的场景标签，用于行为样本聚合。"""
    tags = [request.trigger_kind or "unknown"]
    text = str(request.text or "")
    if "?" in text or "？" in text:
        tags.append("question")
    if request.images:
        tags.append("image")
    if request.quoted_text:
        tags.append("quote")
    if request.mentions:
        tags.append("mention_other")
    if request.batched:
        tags.append("batched")
    return tags


def mood_line(mood: ChatState | None, user: ChatState | None) -> str:
    """生成不超过 50 字的状态行。"""
    valence = mood.valence if mood is not None else 0.0
    if valence >= 0.3:
        feeling = "心情不错"
    elif valence <= -0.3:
        feeling = "心情低落"
    else:
        feeling = "心情平静"
    familiarity = user.familiarity if user is not None else 0.0
    if familiarity >= 0.8:
        level = "很熟"
    elif familiarity >= 0.5:
        level = "熟悉"
    elif familiarity >= 0.2:
        level = "一般"
    else:
        level = "陌生"
    return f"现在{feeling}；对当前用户熟悉度{level}。"


def _scene_text(value: str) -> str:
    try:
        tags = json.loads(value)
    except (TypeError, ValueError):
        tags = []
    if not isinstance(tags, list):
        return "一般"
    clean = [str(tag) for tag in tags if tag]
    return "+".join(clean) if clean else "一般"


def behavior_line(rows: list[ChatBehavior]) -> str:
    """把聚合结果渲染成 Planner 可读的一行提示。"""
    parts = [
        f"{_scene_text(row.scene_tags)} → {row.action}（{row.count} 次，续聊率 {row.confidence:.0%}）" for row in rows
    ]
    return "行为参考：" + "；".join(parts)


async def planner_hints(request: ChatRequest) -> str:
    """读取状态与行为聚合；失败只跳过提示，不影响规划。"""
    if not mood_enabled():
        return ""
    try:
        mood, user = await chat_repository.get_states(request.session_id, request.user_id)
        rows, _ = await chat_repository.behavior_hints(request.session_id)
    except Exception:
        logger.opt(exception=True).debug("[ChatMood] 状态或行为提示读取失败，本轮不注入")
        return ""
    lines = [mood_line(mood, user)]
    if rows:
        lines.append(behavior_line(rows))
    return "\n".join(lines)


async def record_action(request: ChatRequest, action: str) -> None:
    """规划动作发生时记录样本；失败不影响对话。"""
    try:
        await chat_repository.record_behavior_sample(
            request.session_id,
            request.turn_id,
            request.trigger_kind,
            scene_tags(request),
            action,
        )
    except Exception:
        logger.opt(exception=True).debug("[ChatMood] 行为样本落库失败")


async def touch_interaction(request: ChatRequest) -> None:
    """每轮非主动互动提升当前用户的熟悉度；失败不影响对话。"""
    try:
        await chat_repository.touch_interaction(request.session_id, request.user_id)
    except Exception:
        logger.opt(exception=True).debug("[ChatMood] 熟悉度更新失败")


async def behavior_attribute_tick(*, now: datetime | None = None) -> int:
    """观察窗口结束后归因；失败只记日志，下轮重试。"""
    try:
        return await chat_repository.attribute_behavior_samples(now=now)
    except Exception:
        logger.opt(exception=True).debug("[ChatMood] 行为样本归因失败")
        return 0


async def behavior_aggregate_tick() -> int:
    """按租户重建行为聚合；单个租户失败不影响其他租户。"""
    try:
        tenants = await chat_repository.behavior_tenants()
    except Exception:
        logger.opt(exception=True).debug("[ChatMood] 读取行为租户失败")
        return 0
    done = 0
    for tenant in tenants:
        try:
            await chat_repository.aggregate_behavior(tenant)
            done += 1
        except Exception:
            logger.opt(exception=True).warning(f"[ChatMood] 行为聚合失败: {tenant}")
    return done
