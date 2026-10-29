"""原生工具 Planner 的配置、首轮特征和样本落库。"""

from __future__ import annotations

import re
import json
from typing import Any
from dataclasses import dataclass

from nonebot import logger

from .types import ChatRequest
from ..config import get_manager as get_config_manager
from .memory.models import ChatPlannerSample
from .memory.repository import chat_repository

_REPLY_ACTIONS = frozenset({"text_reply", "emoji_reply", "tool"})
_QUESTION = re.compile(r"[?？]|吗|呢|怎么|为什么|如何|多少|哪|谁|什么|是不是|能不能")
_PLACEHOLDER = re.compile(r"^\s*(?:\[(?:图片|表情包|消息类型|语音|视频)[^\]]*\]\s*)+$")
PLANNER_DEFAULTS: dict[str, Any] = {
    "context_summary_max_chars": 6000,
    "passive_replyer": False,
}


def planner_config() -> dict[str, Any]:
    """返回带默认值的规划配置；每次使用时重新读取以支持热更新。"""
    try:
        value = get_config_manager().get_system()["chat"].get("planner")
    except Exception:
        value = None
    merged = dict(PLANNER_DEFAULTS)
    if isinstance(value, dict):
        merged.update(value)
    return merged


@dataclass(frozen=True)
class Plan:
    """规划器选定的首轮动作。"""

    action: str

    @property
    def label(self) -> int:
        """首轮决定回复记 1，其余记 0。"""
        return 1 if self.action in _REPLY_ACTIONS else 0


def plan_features(
    request: ChatRequest,
    *,
    history_len: int = 0,
    memory_chars: int = 0,
) -> dict[str, Any]:
    """记录可用于门控校准的轻量特征；不做检索。"""
    text = str(request.text or "")
    placeholders = bool(_PLACEHOLDER.match(text)) if text else bool(request.images)
    return {
        "trigger_kind": request.trigger_kind,
        "batched": bool(request.batched),
        "message_count": max(1, len(request.source_message_ids)),
        "authors": len(set(request.source_author_ids or [request.user_id])),
        "text_chars": len(text),
        "mentions": len(request.mentions),
        "has_question": bool(_QUESTION.search(text)),
        "placeholder_only": placeholders,
        "has_image": bool(request.images),
        "history_len": int(history_len),
        "memory_chars": int(memory_chars),
    }


async def record_plan_sample(
    request: ChatRequest,
    plan: Plan,
    features: dict[str, Any],
    *,
    provider: str = "",
    model: str = "",
) -> None:
    """每次成功规划写一条样本；失败只降级记录，不影响本轮。"""
    try:
        await chat_repository.save_planner_sample(
            ChatPlannerSample(
                tenant=request.session_id,
                turn_id=request.turn_id,
                trigger_kind=request.trigger_kind,
                action=plan.action,
                label=plan.label,
                features=json.dumps(features, ensure_ascii=False),
                provider=provider,
                model=model,
            )
        )
    except Exception:
        logger.opt(exception=True).debug("[ChatPlanner] 规划样本落库失败")
