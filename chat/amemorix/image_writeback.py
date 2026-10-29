"""适配 MaiBot 062d45c memory_flow_service.ImageMemoryWritebackService 的图片接入。"""

from __future__ import annotations

import json
import base64
import asyncio
import hashlib
from typing import Any
from pathlib import Path
from datetime import timezone

from nonebot import logger

from .runtime import amemorix_runtime
from ...utils.paths import data_dir
from .image_journal import ImageWritebackJournal
from ..message_adapter import load_image_bytes
from ..memory.repository import chat_repository
from .core.image.component_paths import build_chat_external_ref


class ImageWriteback:
    """原图持久化后由可恢复任务写入内核，消息 ID 与组件路径均保留。"""

    def __init__(self) -> None:
        self.journal: ImageWritebackJournal | None = None
        self.task: asyncio.Task | None = None
        self.lock = asyncio.Lock()

    def enabled(self) -> bool:
        """配置使用原有 A_Memorix 图片开关，不新增设置。"""
        return bool(amemorix_runtime.available and amemorix_runtime.config().image_memory.enabled)

    async def start(self) -> None:
        """恢复未完成图片写回任务。"""
        if not self.enabled():
            return
        if self.journal is None:
            self.journal = ImageWritebackJournal(data_dir("ai_chat") / "image_writeback.sqlite3")
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self._worker())

    async def stop(self) -> None:
        """先等待 worker 退出，再关闭持久任务连接。"""
        if self.task is not None:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None
        if self.journal is not None:
            self.journal.close()
            self.journal = None

    async def prepare(
        self, tenant: str, sources: list[str | bytes], component_paths: list[str] | None = None
    ) -> list[dict[str, str]]:
        """按会话缓存原图，避免重启后远程 URL 过期。"""
        if not sources or not self.enabled():
            return []
        maximum = int(amemorix_runtime.config().image_memory.max_bytes)
        folder = data_dir("ai_chat") / "message_images" / hashlib.sha256(tenant.encode("utf-8")).hexdigest()[:24]
        prepared = []
        for index, source in enumerate(sources):
            try:
                if isinstance(source, str) and source.startswith("data:"):
                    content = base64.b64decode(source.split(",", 1)[1], validate=True)
                    if len(content) > maximum:
                        continue
                else:
                    content = await load_image_bytes(source, limit=maximum)
                if not content:
                    continue
                digest = hashlib.sha256(content).hexdigest()
                path = folder / digest

                def save(path=path, content=content) -> None:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    temporary = path.with_suffix(".tmp")
                    temporary.write_bytes(content)
                    temporary.replace(path)

                await asyncio.to_thread(save)
                component_path = (
                    component_paths[index] if component_paths and index < len(component_paths) else str(index)
                )
                prepared.append({"component_path": component_path, "path": str(path), "content_hash": digest})
            except Exception:
                logger.opt(exception=True).warning("[ImageMemory] 原图缓存失败")
        return prepared

    async def enqueue(self, tenant: str, identity: str) -> None:
        """消息落库成功后投递任务，重复消息不重复入队。"""
        if not self.enabled():
            return
        await self.start()
        if self.journal is not None:
            self.journal.enqueue(tenant, identity)

    async def process(self, job: dict[str, Any]) -> None:
        """从真实消息恢复原图并调用内核图片入库。"""
        tenant, identity = str(job["session_id"]), str(job["message_id"])
        message = await chat_repository.message_by_identity(tenant, identity)
        if message is None:
            return
        kernel = await amemorix_runtime.get_kernel()
        if kernel is None:
            raise RuntimeError("图片记忆内核不可用")
        payload = json.loads(message.payload or "{}")
        for item in payload.get("image_memory", []):
            content = await asyncio.to_thread(Path(item["path"]).read_bytes)
            external_ref = build_chat_external_ref(
                chat_id=tenant, message_id=identity, component_path=item["component_path"]
            )
            result = await kernel.image_memory(
                action="ingest",
                image_bytes=content,
                source_kind="chat",
                external_ref=external_ref,
                scope_type="chat",
                chat_id=tenant,
                message_id=identity,
                component_path=item["component_path"],
                occurred_at=message.occurred_at.replace(tzinfo=timezone.utc).timestamp(),
                user_statement=message.content,
            )
            if result.get("success") is False:
                raise RuntimeError("图片记忆入库失败")

    async def _worker(self) -> None:
        """沿用 MaiBot 的重试退避和持久队列，不丢失进行中的任务。"""
        while True:
            interval = max(0.1, float(amemorix_runtime.config().image_memory.job_poll_interval_seconds))
            if not self.enabled() or self.journal is None:
                await asyncio.sleep(interval)
                continue
            job = self.journal.next_job()
            if job is None:
                try:
                    await self.compensate_descriptions()
                except Exception:
                    logger.opt(exception=True).warning("[ImageMemory] 晚到图片描述补偿失败")
                await asyncio.sleep(interval)
                continue
            try:
                await self.process(job)
                self.journal.complete(job["session_id"], job["message_id"])
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.opt(exception=True).warning("[ImageMemory] 图片写回失败，任务将重试")
                self.journal.fail(job, "图片写回失败", 1 + int(amemorix_runtime.config().image_memory.job_max_retries))

    async def compensate_descriptions(self) -> None:
        """沿用 MaiBot 的持久租约，在图片描述晚到后补做摘要。"""
        from .service import ingest_summary

        kernel = await amemorix_runtime.get_kernel()
        if kernel is None:
            return
        config = amemorix_runtime.config()
        claimed = await kernel.image_memory(
            action="claim_description_compensations", limit=int(config.image_memory.job_batch_size)
        )
        if not claimed.get("success"):
            return
        for item in claimed.get("items", []):
            success = False
            try:
                result = await ingest_summary(
                    str(item["chat_id"]),
                    external_id=f"image_description_compensation:{item['occurrence_id']}:{item['description_hash']}",
                    text="",
                    participants=[],
                    time_end=float(item["occurred_at"]) + 0.001,
                    metadata={
                        "generate_from_chat": True,
                        "context_length": int(config.integration.chat_summary_writeback_context_length),
                        "writeback_source": "image_description_compensation",
                        "trigger": "image_description_ready",
                        "image_message_id": str(item["message_id"]),
                        "summary_review_count": 2,
                    },
                )
                success = bool(result.get("success", bool(result.get("stored_ids"))))
            except Exception:
                logger.opt(exception=True).warning("[ImageMemory] 晚到描述摘要生成失败")
            await kernel.image_memory(
                action="complete_description_compensation",
                occurrence_id=str(item["occurrence_id"]),
                description_hash=str(item["description_hash"]),
                lease_token=str(claimed.get("lease_token", "")),
                success=success,
                error="" if success else "图片描述摘要补偿失败",
            )


image_writeback = ImageWriteback()
