"""独立黑话学习，适配 MaiBot 062d45c 的 jargon_learner.py 与 jargon_miner.py。"""

from __future__ import annotations

import json
from typing import Any
from contextlib import asynccontextmanager

from json_repair import repair_json

from .state import is_ready
from .config import jargon_config, tenant_learn_enabled
from .models import ChatMessage
from .prompts import learning_prompt
from .learning import tenant_target, learning_flags
from .filtering import should_learn, sanitize_line
from .repository import chat_repository
from .write_hooks import write_learning
from ..providers.routing import routed_task, fallback_provider

INFERENCE_THRESHOLDS = (4, 8, 25, 100)
MIN_MESSAGES = 10


async def _model_json(model: Any, text: str) -> Any:
    """沿用上游普通生成及 JSON 修复，允许模型返回根数组。"""
    response = await model.chat(messages=[{"role": "user", "content": text}], max_tokens=8192)
    return json.loads(repair_json(response.content))


def inference_due(count: int, last_inference: int) -> bool:
    """在 4、8、25、100 次出现时重新推断释义。"""
    threshold = next((value for value in INFERENCE_THRESHOLDS if value > last_inference), None)
    return threshold is not None and count >= threshold


@asynccontextmanager
async def _lease(tenant: str, provider: Any):
    """复用测试模型或使用既有 learner 路由。"""
    if provider is not None:
        yield provider
    else:
        async with fallback_provider(routed_task("learner", "utils"), tenant) as leased:
            yield leased


async def learn_jargon(tenant: str, *, provider: Any = None, source_messages: list[ChatMessage] | None = None) -> bool:
    """独立提取至少 10 条有效消息中的黑话候选，按阈值进行释义。"""
    if not is_ready():
        return False
    _, enabled = learning_flags(jargon_config().get("learning_list"), tenant_target(tenant))
    if not enabled or not tenant_learn_enabled(await chat_repository.get_session_state(tenant)):
        return False
    cursor = await chat_repository.get_cursor(tenant, "jargon")
    after = int(cursor.cursor_seq) if cursor else 0
    raw = (
        await chat_repository.messages_after(tenant, after, limit=100)
        if source_messages is None
        else sorted(
            (message for message in source_messages if int(message.id or 0) > after),
            key=lambda message: int(message.id or 0),
        )
    )
    messages = [row for row in raw if should_learn(row.content, tenant=tenant, check_duplicate=False)]
    if len(messages) < MIN_MESSAGES:
        return False
    source = {int(row.id or 0): row for row in messages}
    known = await chat_repository.list_jargon(tenant, limit=500)
    text = "\n".join(
        f'<message id="{identifier}">USER {row.sender_name}: {row.content}</message>'
        for identifier, row in source.items()
    )
    async with _lease(tenant, provider) as model:
        data = await _model_json(model, learning_prompt("learn_jargon", chat_str=text))
        if not isinstance(data, list):
            return False
        candidates: dict[str, list[int]] = {}
        for item in data:
            if not isinstance(item, dict):
                continue
            term = sanitize_line(item.get("content"), 30)
            if len(term) < 2:
                continue
            identifiers = [
                identifier for identifier, row in source.items() if term.casefold() in row.content.casefold()
            ]
            if identifiers:
                candidates[term] = identifiers
        for row in known:
            identifiers = [
                identifier for identifier, message in source.items() if row.normalized in message.content.casefold()
            ]
            if row.normalized and identifiers:
                candidates.setdefault(row.term, identifiers)
        saved = await write_learning(
            tenant,
            "jargon",
            lambda: chat_repository.save_jargon_batch(
                tenant,
                candidates,
                cursor_seq=int(raw[-1].id or 0),
                expected_cursor=after,
                evidence_ids=tuple(source),
            ),
            candidates=candidates,
            cursor_seq=int(raw[-1].id or 0),
        )
        if not saved:
            return False
        for row in await chat_repository.list_jargon(tenant, limit=500):
            if not inference_due(row.count, row.last_inference_count):
                continue
            identifiers = list(json.loads(row.evidence or "[]"))
            evidence = await chat_repository.jargon_evidence(tenant, identifiers)
            context = "\n".join(message.content for message in evidence)
            contextual = await _model_json(
                model,
                learning_prompt(
                    "jargon_inference_with_context",
                    content=row.term,
                    bot_name="SELF",
                    raw_content_list=context,
                    previous_meaning_section=f"之前的释义：{row.meaning}" if row.meaning else "",
                    previous_meaning_instruction="如有更可靠的上下文请修正之前的释义。" if row.meaning else "",
                ),
            )
            if not isinstance(contextual, dict):
                continue
            meaning = sanitize_line(contextual.get("meaning"), 1000)
            if contextual.get("no_info") or not meaning:
                await write_learning(
                    tenant,
                    "jargon_inference",
                    lambda: chat_repository.save_jargon_inference(
                        tenant,
                        int(row.id or 0),
                        expected_count=row.count,
                        meaning="",
                        accepted=False,
                    ),
                    item_id=int(row.id or 0),
                    meaning="",
                    accepted=False,
                )
                continue
            isolated = await _model_json(model, learning_prompt("jargon_inference_content_only", content=row.term))
            if not isinstance(isolated, dict):
                continue
            compared = await _model_json(
                model,
                learning_prompt(
                    "jargon_compare_inference",
                    inference1=json.dumps(contextual, ensure_ascii=False),
                    inference2=json.dumps(isolated, ensure_ascii=False),
                ),
            )
            if not isinstance(compared, dict) or not isinstance(compared.get("is_similar"), bool):
                continue
            await write_learning(
                tenant,
                "jargon_inference",
                lambda: chat_repository.save_jargon_inference(
                    tenant,
                    int(row.id or 0),
                    expected_count=row.count,
                    meaning=meaning,
                    accepted=not compared["is_similar"],
                ),
                item_id=int(row.id or 0),
                meaning=meaning,
                accepted=not compared["is_similar"],
            )
    return True
