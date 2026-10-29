"""适配 MaiBot 062d45c 的 send_emoji 与 show_emoji_list，候选限定在当前会话。"""

from __future__ import annotations

import asyncio
from uuid import uuid4
from typing import Any
from itertools import count
from dataclasses import replace

from nonebot import logger
from pydantic import BaseModel

from ..emoji import emoji_by_hash, emoji_catalog, emoji_collage, emoji_grid_shape
from ..types import ChatResponse
from ...config import get_manager as get_config_manager
from ..memory.prompts import learning_prompt
from ..memory.repository import chat_repository
from ..providers.routing import fallback_provider, candidate_providers
from ..behavior.reply_gate import default_reply_gate_registry
from ..message_receive.sender import deliver_emoji

_indices = count(1)


class EmojiSelectionResult(BaseModel):
    """上游表情选择输出，缺省编号为首项。"""

    emoji_index: int = 1
    reason: str = ""


def selector_task(tenant: str) -> str:
    """复用现有路由：优先 emoji，其次已指定为视觉模型的 Planner，最后 vision。"""
    routing = get_config_manager().get_system()["chat"].get("model_routing") or {}
    if routing.get("emoji"):
        return "emoji"
    vision = str(routing.get("vision") or "")
    if vision:
        return "planner" if candidate_providers("planner", tenant)[0] == vision else "vision"
    raise ValueError("未配置表情选择的视觉模型")


def planner_has_vision(tenant: str) -> bool:
    """使用现有 vision 路由确定 Planner 是否配置了可看图的模型。"""
    routing = get_config_manager().get_system()["chat"].get("model_routing") or {}
    return bool(routing.get("vision")) and candidate_providers("planner", tenant)[0] == routing["vision"]


def selected_hash(request: Any, index: Any, history: list[dict[str, Any]]) -> str | None:
    """候选编号必须来自当前会话已展示的拼图快照。"""
    if type(index) is not int or index < 1:
        return None
    mappings = [
        request.metadata.get("planner_emoji_candidates", {}),
        *(message.get("_emoji_candidates", {}) for message in history),
    ]
    return next((mapping[str(index)] for mapping in mappings if str(index) in mapping), None)


async def show_candidates(request: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """把编号拼图放进规划上下文，不发送给聊天对象。"""
    if not planner_has_vision(request.session_id):
        return {"ok": False, "error": "show_emoji_list 需要 Planner 视觉能力"}, []
    rows = await emoji_catalog(request.session_id)
    if not rows:
        return {"ok": False, "error": "当前会话没有可用表情"}, []
    identifiers = [next(_indices) for _ in rows]
    image = await asyncio.to_thread(emoji_collage, rows, start_index=identifiers[0])
    if not image:
        return {"ok": False, "error": "无法生成表情候选图"}, []
    mapping = request.metadata.setdefault("planner_emoji_candidates", {})
    mapping.update({str(identifier): row.hash for identifier, row in zip(identifiers, rows)})
    text = (
        f"表情候选图：序号 {identifiers[0]} 到 {identifiers[-1]}；"
        "在 reply 或 set_reply_options 的 attach_emoji 中填写工具编号。"
    )
    return {"ok": True, "content": text}, [
        {
            "role": "user",
            "content": [{"type": "text", "text": text}, {"type": "image_url", "image_url": {"url": image}}],
            "_emoji_candidates": {str(identifier): row.hash for identifier, row in zip(identifiers, rows)},
            "_planner_internal": True,
        }
    ]


async def send_selected(service: Any, context: Any, reasoning: str, *, target_id: str = "") -> dict[str, Any]:
    """视觉选择一张表情并通过现有发送适配器交付。"""
    request = context.request
    del reasoning
    if target_id and target_id != (request.message_id or request.dedupe_key):
        if await chat_repository.message_by_identity(request.session_id, target_id) is None:
            return {"ok": False, "error": "表情回复目标不属于当前会话"}
    rows = await emoji_catalog(request.session_id)
    if not rows:
        return {"ok": False, "error": "当前会话没有可用表情"}
    matcher = getattr(context.runtime, "matcher", None)
    bot = getattr(context.runtime, "bot", None)
    if matcher is None or bot is None:
        return {"ok": False, "error": "当前没有可用的发送目标"}
    image = await asyncio.to_thread(emoji_collage, rows)
    if not image:
        return {"ok": False, "error": "无法生成表情候选图"}
    grid_rows, grid_columns = emoji_grid_shape(len(rows))
    prompt = [
        {
            "role": "system",
            "content": learning_prompt(
                "emoji_selection",
                emoji_count=len(rows),
                grid_rows=grid_rows,
                grid_columns=grid_columns,
            ),
        }
    ]
    history = [*service.session_store.get(request.session_id), *request.metadata.get("planner_trace", [])]
    selected = []
    for message in history:
        role = message.get("role")
        if role in {"user", "assistant"} and not message.get("tool_calls"):
            selected.append({"role": role, "content": message.get("content") or ""})
        elif role == "tool":
            selected.append({"role": "user", "content": f"[工具结果] {message.get('content') or ''}"})
    prompt.extend(selected[-12:])
    prompt.extend(
        [
            {
                "role": "user",
                "content": f"[选择任务]\n候选总数: {len(rows)}\n拼图布局: {grid_rows}x{grid_columns}\n请只输出 JSON。",
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "[表情包拼图候选]"},
                    {"type": "image_url", "image_url": {"url": image}},
                ],
            },
        ]
    )
    try:
        task = selector_task(request.session_id)
        async with fallback_provider(task, request.session_id) as provider:
            response = await asyncio.wait_for(
                provider.chat(prompt, temperature=0.7, max_tokens=16384 if task == "planner" else 8192),
                timeout={"planner": 180, "vision": 240}.get(task, 120),
            )
        try:
            payload = EmojiSelectionResult.model_validate_json(response.content)
            index, reason = payload.emoji_index, payload.reason.strip()
        except (ValueError, TypeError, AttributeError):
            logger.warning("[ChatEmoji] 选择结果不是合法 JSON，按上游规则选择候选首项")
            index, reason = 1, ""
        if isinstance(index, bool) or not isinstance(index, int) or not 1 <= index <= len(rows):
            index = 1
        if not service.is_current(request.session_id, context.epoch):
            return {"ok": False, "error": "本轮已失效"}
        row = await emoji_by_hash(request.session_id, rows[index - 1].hash)
        if row is None:
            return {"ok": False, "error": "选中的表情已不可用"}
        sending = replace(
            request,
            turn_id=f"emoji-{uuid4().hex}",
            accepted_epoch=context.epoch,
            metadata={**request.metadata, "history_recorded": True, "history_appended": True},
        )
        persisted = await service._persist_turn_start(sending, sending.text, context.epoch)
        delivery = ChatResponse(
            text="[表情包]", epoch=context.epoch, metadata={"turn_db_id": persisted[0]} if persisted is not None else {}
        )
        sent = await deliver_emoji(service, sending, delivery, context.runtime, matcher, row, 1, target_id=target_id)
        if sent and request.is_group:
            default_reply_gate_registry.get(request.session_id).record_reply()
    except Exception:
        logger.opt(exception=True).warning("[ChatEmoji] 规划表情发送失败")
        return {"ok": False, "error": "表情发送失败"}
    return {
        "ok": sent,
        "content": reason or ("表情包发送成功" if sent else "表情发送失败"),
        "description": row.description if sent else "",
    }
