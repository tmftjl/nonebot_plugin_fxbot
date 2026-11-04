"""聊天时的 A_Memorix 记忆注入。

人物画像来自 A_Memorix 画像服务；启发式记忆按 MaiBot
``src/maisaka/memory/heuristic_injector.py`` 的触发条件与过滤规则移植。
"""

from __future__ import annotations

from time import time
from typing import Any
from dataclasses import field, dataclass

from nonebot import logger

from ..types import ChatRequest
from .people import session_parts
from .runtime import amemorix_runtime
from .service import search, person_profile
from .messages import count_messages, recent_messages
from ._host.llm import LLMServiceClient
from ._host.person_db import person_scope, make_person_id
from ._host.prompt_i18n import load_prompt
from .core.utils.profile_text import build_profile_injection_text

HEURISTIC_MARKER = "【启发式记忆-内部参考】"
PROFILE_MARKER = "【人物画像-内部参考】"
_CHAT_SUMMARY_PREFIX = "chat_summary:"
_PERSON_FACT_PREFIX = "person_fact:"


@dataclass
class _RecallState:
    last_recall_at: float = 0.0
    last_message_count: int = 0
    cached_reference: str = ""
    cache_expires_at: float = 0.0


@dataclass
class _RecallContext:
    tenant: str
    platform: str
    group_id: str
    user_id: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    active_person_ids: set[str] = field(default_factory=set)
    total_message_count: int = 0


class HeuristicRecall:
    def __init__(self) -> None:
        self._states: dict[str, _RecallState] = {}
        self._client = LLMServiceClient(task_name="utils", request_type="heuristic_memory_impression")

    async def build(self, request: ChatRequest) -> str:
        config = amemorix_runtime.config().integration
        if not config.heuristic_memory_recall_enabled:
            return ""
        tenant = request.session_id
        window_size = max(1, int(config.heuristic_memory_recall_window_size))
        total = await count_messages(tenant)
        if total < window_size:
            return ""
        state = self._states.setdefault(tenant, _RecallState())
        now = time()
        cache_ttl = int(config.heuristic_memory_recall_cache_ttl_seconds)
        if state.cached_reference and cache_ttl > 0 and now < state.cache_expires_at:
            return state.cached_reference
        if state.last_message_count > 0:
            if now - state.last_recall_at < int(config.heuristic_memory_recall_min_interval_seconds):
                return ""
            if total - state.last_message_count < int(config.heuristic_memory_recall_min_new_messages):
                return ""

        messages = await recent_messages(tenant, window_size)
        if len(messages) < window_size:
            return ""
        platform = request.platform or session_parts(tenant)[0]
        context = _RecallContext(
            tenant=tenant,
            platform=platform,
            group_id=str(request.group_id or ""),
            user_id=request.user_id,
            messages=messages,
            active_person_ids=self._active_person_ids(request, messages, platform),
            total_message_count=total,
        )
        try:
            impression = await self._impression(context)
            if not impression:
                return ""
            reference = await self._reference(impression, context)
        except Exception as exc:
            logger.opt(exception=True).debug(f"[A_Memorix] 启发式记忆拉起失败: {exc}")
            return ""

        state.last_recall_at = now
        state.last_message_count = total
        state.cached_reference = reference
        state.cache_expires_at = now + cache_ttl if cache_ttl > 0 else 0.0
        return reference

    @staticmethod
    def _active_person_ids(request: ChatRequest, messages: list[dict[str, Any]], platform: str) -> set[str]:
        person_ids = set()
        for user_id in [request.user_id, *(ref.user_id for ref in request.mentions)]:
            if user_id:
                person_ids.add(make_person_id(request.session_id, platform, str(user_id)))
        for message in messages:
            if message["sender_id"]:
                person_ids.add(make_person_id(request.session_id, platform, message["sender_id"]))
        return person_ids

    async def _impression(self, context: _RecallContext) -> str:
        chat_type = "group" if context.group_id else "private"
        identity = [f"chat_type: {chat_type}", f"platform: {context.platform}"]
        if context.group_id:
            identity.append(f"group_id: {context.group_id}")
        if context.user_id:
            identity.append(f"user_id: {context.user_id}")
        window = []
        for message in context.messages:
            text = " ".join(str(message["content"] or "").split())
            if not text:
                continue
            window.append(f"- {message['sender_name'] or message['sender_id']}: {text[:220]}")
        prompt = load_prompt(
            "heuristic_memory_impression",
            chat_identity="\n".join(identity),
            message_window="\n".join(window) or "no text messages available",
        )
        completion = await self._client.generate_response(prompt)
        return str(getattr(completion, "response", "") or "").strip()

    async def _reference(self, impression: str, context: _RecallContext) -> str:
        config = amemorix_runtime.config().integration
        limit = max(1, int(config.heuristic_memory_recall_limit))
        cross_chat = bool(config.heuristic_memory_cross_chat_enabled)
        result = await search(
            context.tenant,
            impression,
            limit=min(20, max(limit * 4, limit)),
            chat_id="" if cross_chat else context.tenant,
            respect_filter=not cross_chat,
            user_id=context.user_id,
            group_id=context.group_id,
        )
        if not result.get("success") or result.get("filtered"):
            return ""
        hits: list[str] = []
        seen: set[str] = set()
        for hit in result.get("hits", []) or []:
            if not self._allowed(hit, context):
                continue
            key = str(hit.get("hash") or hit.get("content") or "")
            if key in seen:
                continue
            seen.add(key)
            content = " ".join(str(hit.get("content") or "").split())
            if content:
                hits.append(content[:180])
            if len(hits) >= limit:
                break
        if not hits:
            return ""
        lines = [
            HEURISTIC_MARKER,
            "以下是从当前聊天印象自然拉起的长期记忆，只作为推理参考，不要逐字复述。",
            "",
            *[f"{index}. {text}" for index, text in enumerate(hits, start=1)],
        ]
        reference = "\n".join(lines)
        return reference[: int(config.heuristic_memory_recall_max_chars)]

    def _allowed(self, hit: dict[str, Any], context: _RecallContext) -> bool:
        metadata = hit.get("metadata") if isinstance(hit.get("metadata"), dict) else {}
        source = str(metadata.get("source") or hit.get("source") or "").strip()
        source_type = str(metadata.get("source_type") or "").strip()
        chat_id = str(metadata.get("chat_id") or "").strip()
        person_id = str(metadata.get("person_id") or "").strip()
        if hit.get("type") == "episode":
            return source == f"{_CHAT_SUMMARY_PREFIX}{context.tenant}"
        if source_type == "person_fact" or source.startswith(_PERSON_FACT_PREFIX):
            if not person_id and source.startswith(_PERSON_FACT_PREFIX):
                person_id = source[len(_PERSON_FACT_PREFIX) :].strip()
            return bool(person_id and person_id in context.active_person_ids)
        if source_type == "chat_summary" or source.startswith(_CHAT_SUMMARY_PREFIX):
            source_tenant = chat_id or source[len(_CHAT_SUMMARY_PREFIX) :].strip()
            return source_tenant == context.tenant
        return bool(chat_id and chat_id == context.tenant)


heuristic_recall = HeuristicRecall()


def _profile_block(payload: dict[str, Any], fallback_name: str) -> str:
    text = str(payload.get("summary") or payload.get("profile_text") or "").strip()
    if not text:
        return ""
    injected = build_profile_injection_text(text, include_uncertain_fallback=False)
    if not injected:
        return ""
    name = str(payload.get("person_name") or fallback_name or "").strip()
    return f"{name}：\n  {injected[:900]}"


async def _profiles(request: ChatRequest, max_profiles: int) -> str:
    tenant = request.session_id
    platform, group_id = session_parts(tenant)
    del group_id
    candidates: list[tuple[str, str]] = [(str(request.user_id or ""), str(request.user_name or ""))]
    for ref in request.mentions:
        candidates.append((str(ref.user_id or ""), str(ref.name or "")))
    blocks: list[str] = []
    seen: set[str] = set()
    for user_id, name in candidates:
        if not user_id:
            continue
        person_id = make_person_id(tenant, platform, user_id)
        if person_id in seen:
            continue
        seen.add(person_id)
        payload = await person_profile(tenant, person_id, limit=4)
        if not payload.get("success"):
            continue
        block = _profile_block(payload, name or user_id)
        if block:
            blocks.append(block)
        if len(blocks) >= max_profiles:
            break
    if not blocks:
        return ""
    return (
        f"{PROFILE_MARKER}\n以下内容仅供内部推理，不要向用户逐字复述。\n\n"
        + "\n\n".join(blocks)
        + "\n\n使用时把它当作对当前人物的背景理解；若与当前对话冲突，以当前对话为准。"
    )


async def memory_block(request: ChatRequest) -> str:
    """构造本轮注入的记忆块；未启用或内核不可用时返回空串。"""
    config = amemorix_runtime.config()
    if not config.plugin.enabled:
        return ""
    kernel = await amemorix_runtime.get_kernel()
    if kernel is None:
        return ""
    sections: list[str] = []
    with person_scope(request.session_id):
        if config.integration.enable_person_profile_injection:
            try:
                profile = await _profiles(request, int(config.integration.person_profile_injection_max_profiles))
            except Exception:
                logger.opt(exception=True).debug("[A_Memorix] 人物画像注入失败")
                profile = ""
            if profile:
                sections.append(profile)
        if config.integration.heuristic_memory_recall_enabled:
            try:
                reference = await heuristic_recall.build(request)
            except Exception:
                logger.opt(exception=True).debug("[A_Memorix] 启发式记忆注入失败")
                reference = ""
            if reference:
                sections.append(reference)
    return "\n\n".join(sections)
