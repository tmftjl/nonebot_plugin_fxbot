"""聊天摘要与人物事实写回。

摘要触发条件对齐 MaiBot ``src/services/memory_flow_service.py``：同一聊天流累计
新增消息达到阈值后，让 A_Memorix 从 ``chat_message`` 读取最近窗口生成摘要。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from datetime import datetime
from typing import Any

from json_repair import repair_json
from nonebot import logger

from ...utils.tz import to_naive_utc
from ._host.llm import LLMServiceClient
from ._host.person_db import make_person_id, person_scope
from .messages import count_messages, sender_messages
from .people import remember_person, session_parts
from .runtime import amemorix_runtime
from .service import ingest_summary, ingest_text

FACT_PROMPT = """你要从用户原始发言中提取“关于{person_name}的稳定事实”。

目标人物：{person_name}
用户原始发言证据：
{evidence}

机器人回复：
{reply}

请只提取满足以下条件的事实：
1. 事实值必须能被“目标人物原始发言”直接支持，不能只来自机器人回复。
2. 明确是关于目标人物本人的信息。
3. 具有相对稳定性，可以作为长期记忆保存。
4. 用简洁中文陈述句表达，并改写成第三人称。
5. 不确定、猜测、反问、临时安排、机器人的建议都不要提取。

严格输出 JSON 数组，每项包含 fact、evidence_message_id、evidence_quote。证据片段必须逐字摘自目标人物原始发言。没有可写入的事实时输出 []。"""


class ChatWriteback:
    def __init__(self) -> None:
        self._summary_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
        self._fact_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=64)
        self._summary_task: asyncio.Task | None = None
        self._fact_task: asyncio.Task | None = None
        self._states: dict[str, int] = {}
        self._stopping = False
        self._fact_client = LLMServiceClient(task_name="utils", request_type="A_Memorix.person_fact_writeback")

    async def start(self) -> None:
        if self._stopping:
            return
        if self._summary_task is None or self._summary_task.done():
            self._summary_task = asyncio.create_task(self._summary_loop(), name="amemorix_summary_writeback")
        if self._fact_task is None or self._fact_task.done():
            self._fact_task = asyncio.create_task(self._fact_loop(), name="amemorix_person_fact_writeback")

    async def stop(self) -> None:
        self._stopping = True
        for task in (self._summary_task, self._fact_task):
            if task is None:
                continue
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._summary_task = None
        self._fact_task = None
        self._stopping = False

    def on_message(
        self,
        tenant: str,
        *,
        platform: str,
        user_id: str,
        user_name: str,
        group_id: str = "",
        occurred_at: datetime | None = None,
    ) -> None:
        if not self._enabled("chat_summary_writeback_enabled"):
            return
        self._put(
            self._summary_queue,
            {
                "tenant": tenant,
                "platform": platform,
                "user_id": user_id,
                "user_name": user_name,
                "group_id": group_id,
                "occurred_at": occurred_at,
            },
            "聊天摘要",
        )

    def on_reply(
        self,
        tenant: str,
        *,
        platform: str,
        user_id: str,
        user_name: str,
        group_id: str,
        reply_text: str,
        occurred_at: datetime | None = None,
    ) -> None:
        if not str(reply_text or "").strip() or self._ephemeral(reply_text):
            return
        if not self._enabled("person_fact_writeback_enabled"):
            return
        self._put(
            self._fact_queue,
            {
                "tenant": tenant,
                "platform": platform,
                "user_id": user_id,
                "user_name": user_name,
                "group_id": group_id,
                "reply_text": str(reply_text),
                "occurred_at": occurred_at,
            },
            "人物事实",
        )

    def _enabled(self, field: str) -> bool:
        if not amemorix_runtime.available:
            return False
        return bool(getattr(amemorix_runtime.config().integration, field, False))

    @staticmethod
    def _put(queue: asyncio.Queue, item: dict[str, Any], label: str) -> None:
        try:
            queue.put_nowait(item)
        except asyncio.QueueFull:
            logger.warning(f"[A_Memorix] {label}写回队列已满，跳过本次")

    async def _summary_loop(self) -> None:
        while True:
            item = await self._summary_queue.get()
            try:
                await self._handle_summary(item)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.opt(exception=True).warning("[A_Memorix] 聊天摘要写回失败")
            finally:
                self._summary_queue.task_done()

    async def _fact_loop(self) -> None:
        while True:
            item = await self._fact_queue.get()
            try:
                await self._handle_fact(item)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.opt(exception=True).warning("[A_Memorix] 人物事实写回失败")
            finally:
                self._fact_queue.task_done()

    async def _handle_summary(self, item: dict[str, Any]) -> None:
        tenant = str(item["tenant"] or "")
        if not tenant:
            return
        occurred_at = item.get("occurred_at")
        end_time = to_naive_utc(occurred_at) if isinstance(occurred_at, datetime) else None
        total = await count_messages(tenant, end_time)
        if total <= 0:
            return
        config = amemorix_runtime.config().integration
        threshold = max(1, int(config.chat_summary_writeback_message_threshold))
        last = self._states.get(tenant)
        if last is None:
            last = await self._checkpoint(tenant, total)
            self._states[tenant] = last
        pending = max(0, total - last)
        if pending < threshold:
            return
        configured = max(1, int(config.chat_summary_writeback_context_length))
        context_length = min(configured, pending)
        result = await ingest_summary(
            tenant,
            external_id=f"chat_auto_summary:{tenant}:{total}",
            text="",
            time_end=float(end_time.timestamp()) if end_time else None,
            metadata={
                "generate_from_chat": True,
                "context_length": context_length,
                "configured_context_length": configured,
                "writeback_source": "chat.amemorix.writeback",
                "trigger": "message_threshold",
                "previous_trigger_message_count": last,
                "pending_message_count": pending,
                "trigger_message_count": total,
                "summary_review_count": 2,
            },
            respect_filter=True,
            user_id=str(item.get("user_id") or ""),
            group_id=str(item.get("group_id") or ""),
        )
        if not result.get("success"):
            logger.warning(f"[A_Memorix] 聊天摘要写回失败: tenant={tenant} detail={result.get('detail', '')}")
            return
        self._states[tenant] = total
        logger.info(
            f"[A_Memorix] 聊天摘要写回成功: tenant={tenant} total={total} context_length={context_length}"
        )

    async def _checkpoint(self, tenant: str, total: int) -> int:
        kernel = await amemorix_runtime.get_kernel()
        if kernel is None or kernel.metadata_store is None:
            return 0
        try:
            count = int(kernel.metadata_store.get_summary_checkpoint_count(tenant) or 0)
            paragraphs = kernel.metadata_store.get_paragraphs_by_source(f"chat_summary:{tenant}")
        except Exception:
            return 0
        if not paragraphs:
            return min(total, count)
        latest = max(paragraphs, key=lambda row: float(row.get("created_at") or 0.0))
        metadata = latest.get("metadata")
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except ValueError:
                metadata = {}
        trigger = int(metadata.get("trigger_message_count") or 0) if isinstance(metadata, dict) else 0
        return min(total, max(trigger, count)) if trigger else total

    async def _handle_fact(self, item: dict[str, Any]) -> None:
        tenant = str(item["tenant"] or "")
        user_id = str(item["user_id"] or "")
        if not tenant or not user_id:
            return
        platform = str(item.get("platform") or "") or session_parts(tenant)[0]
        person_id = make_person_id(tenant, platform, user_id)
        name = str(item.get("user_name") or "").strip() or user_id
        await remember_person(
            tenant,
            platform,
            user_id,
            name,
            group_id=str(item.get("group_id") or ""),
        )
        occurred_at = item.get("occurred_at")
        before = to_naive_utc(occurred_at) if isinstance(occurred_at, datetime) else None
        evidence = await sender_messages(tenant, user_id, 3, before=before)
        if not evidence:
            return
        evidence_text = "\n".join(
            f"- [消息ID: {row['message_id']}] {row['content']}" if row["message_id"] else f"- {row['content']}"
            for row in evidence
        )
        prompt = FACT_PROMPT.format(person_name=name, evidence=evidence_text, reply=item["reply_text"])
        completion = await self._fact_client.generate_response(prompt)
        facts = _parse_facts(str(getattr(completion, "response", "") or ""))
        if not facts:
            return
        message_ids = [row["message_id"] for row in evidence if row["message_id"]]
        for fact in facts:
            digest = hashlib.md5(f"{person_id}|{tenant}|{fact}".encode()).hexdigest()
            with person_scope(tenant):
                await ingest_text(
                    tenant,
                    external_id=f"person_fact:{person_id}:{digest}",
                    source_type="person_fact",
                    text=fact,
                    person_ids=[person_id],
                    participants=[name],
                    tags=["person_fact"],
                    metadata={
                        "person_id": person_id,
                        "person_name": name,
                        "writeback_source": "chat.amemorix.writeback",
                        "evidence_source": "user_supported",
                        "evidence_message_ids": message_ids,
                        "fact_claim": {
                            "authority": "direct_user",
                            "stability": "stable",
                            "profile_section": "stable_facts",
                        },
                    },
                    respect_filter=True,
                )

    @staticmethod
    def _ephemeral(text: str) -> bool:
        content = str(text or "").strip()
        markers = ("哈哈", "好的", "收到", "嗯嗯", "晚安", "早安", "拜拜", "谢谢", "在吗", "？")
        return len(content) <= 8 and any(marker in content for marker in markers)


def _parse_facts(raw: str) -> list[str]:
    text = str(raw or "").strip()
    if not text:
        return []
    try:
        repaired = repair_json(text)
        payload = json.loads(repaired) if isinstance(repaired, str) else repaired
    except Exception:
        return []
    if not isinstance(payload, list):
        return []
    facts: list[str] = []
    for item in payload:
        fact = str(item.get("fact") if isinstance(item, dict) else item or "").strip()
        if len(fact) < 4 or fact in facts:
            continue
        facts.append(fact)
    return facts[:5]


chat_writeback = ChatWriteback()
