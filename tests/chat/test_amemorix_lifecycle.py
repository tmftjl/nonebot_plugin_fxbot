"""长期记忆的真实资源释放、异常关闭和摘要时间边界。"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from src.plugins.nonebot_plugin_fxbot.chat.tools import amemorix as memory_tools
from src.plugins.nonebot_plugin_fxbot.chat.amemorix import inject as memory_inject, runtime, service, writeback
from src.plugins.nonebot_plugin_fxbot.chat.tools.types import ToolContext
from src.plugins.nonebot_plugin_fxbot.chat.amemorix.core.runtime import sdk_memory_kernel as kernel_module
from src.plugins.nonebot_plugin_fxbot.chat.amemorix.core.runtime.models import KernelSearchRequest
from src.plugins.nonebot_plugin_fxbot.chat.amemorix._host.amemorix_config import AMemorixConfig


class KernelResourceTests(unittest.IsolatedAsyncioTestCase):
    """关闭后不依赖垃圾回收或延迟等待，即可释放完整数据目录。"""

    def kernel(self):
        """构造使用全新临时存储的真实内核。"""
        folder = tempfile.TemporaryDirectory(prefix="fxbot-kernel-resource-")
        self.addCleanup(folder.cleanup)
        config = AMemorixConfig().model_dump()
        config["storage"]["data_dir"] = folder.name
        kernel = kernel_module.SDKMemoryKernel(plugin_root=Path("chat/amemorix").resolve(), config=config)
        return folder, kernel

    async def test_shutdown_releases_sparse_and_metadata_connections(self) -> None:
        folder, kernel = self.kernel()
        try:
            await kernel.initialize()
            result = await kernel.ingest_text(
                external_id="test-close",
                source_type="test",
                text="测试人物喜欢登山",
                chat_id="test:close",
                respect_filter=False,
            )
            self.assertEqual(len(result["stored_ids"]), 1)
            sparse = kernel.sparse_index
            manager = kernel.metadata_store._connection_manager
            self.assertTrue(sparse.loaded)
        finally:
            await kernel.shutdown()
        self.assertIsNone(sparse._conn)
        self.assertTrue(manager.closed)
        self.assertFalse(kernel._runtime_writer_lock.held)
        folder.cleanup()

    async def test_persistence_failure_still_releases_connections(self) -> None:
        folder, kernel = self.kernel()
        await kernel.initialize()
        sparse = kernel.sparse_index
        manager = kernel.metadata_store._connection_manager
        with patch.object(kernel, "_persist", side_effect=RuntimeError("synthetic persist failure")):
            with self.assertRaises(RuntimeError):
                await kernel.shutdown()
        self.assertFalse(sparse.loaded)
        self.assertTrue(manager.closed)
        self.assertFalse(kernel._runtime_writer_lock.held)
        folder.cleanup()

    async def test_person_fact_is_searchable_only_in_its_own_chat(self) -> None:
        folder, kernel = self.kernel()
        try:
            await kernel.initialize()
            stored = await kernel.ingest_text(
                external_id="test-fact-scope",
                source_type="person_fact",
                text="测试小林常住苏州，平时喜欢爬山。",
                chat_id="test:fact-owner",
                person_ids=["test-person"],
                metadata={"person_id": "test-person", "evidence_source": "user_supported"},
                respect_filter=False,
            )
            scope = kernel._get_search_hit_service()._resolve_retrieval_scope("test:fact-owner")
            self.assertIn(stored["stored_ids"][0], scope.paragraph_ids)
            own = await kernel.search_memory(
                KernelSearchRequest(query="苏州", chat_id="test:fact-owner", respect_filter=False)
            )
            other = await kernel.search_memory(
                KernelSearchRequest(query="苏州", chat_id="test:other", respect_filter=False)
            )
            self.assertTrue(own["hits"])
            self.assertEqual(other["hits"], [])
            proxy = SimpleNamespace(available=True, get_kernel=AsyncMock(return_value=kernel))
            with (
                patch.object(service, "amemorix_runtime", proxy),
                patch.object(memory_tools, "_enabled", return_value=True),
                patch.object(
                    memory_tools.amemorix_runtime,
                    "config",
                    return_value=SimpleNamespace(integration=SimpleNamespace(memory_query_default_limit=5)),
                ),
            ):
                response = await service.search("test:fact-owner", "苏州", respect_filter=False)
                self.assertTrue(response["success"])
                text = await memory_tools.query_memory_tool(
                    ToolContext(session_id="test:fact-owner"), None, query="苏州", respect_filter=False
                )
                self.assertIn("苏州", text)
                self.assertNotIn("暂时不可用", text)
                config = SimpleNamespace(
                    integration=SimpleNamespace(
                        heuristic_memory_recall_limit=3,
                        heuristic_memory_cross_chat_enabled=False,
                        heuristic_memory_recall_max_chars=1200,
                    )
                )
                context = memory_inject._RecallContext(
                    tenant="test:fact-owner",
                    platform="test",
                    group_id="",
                    user_id="test-user",
                    active_person_ids={"test-person"},
                )
                with patch.object(memory_inject.amemorix_runtime, "config", return_value=config):
                    reference = await memory_inject.heuristic_recall._reference("苏州", context)
                self.assertIn("苏州", reference)
        finally:
            await kernel.shutdown()
        folder.cleanup()

    async def test_initialization_failure_closes_preheated_index(self) -> None:
        folder, kernel = self.kernel()
        created = []
        original = kernel_module.SparseBM25Index

        def sparse(*args, **kwargs):
            instance = original(*args, **kwargs)
            created.append(instance)
            return instance

        with (
            patch.object(kernel_module, "SparseBM25Index", side_effect=sparse),
            patch.object(
                kernel, "_start_background_tasks", AsyncMock(side_effect=RuntimeError("synthetic startup failure"))
            ),
        ):
            with self.assertRaises(RuntimeError):
                await kernel.initialize()
        self.assertTrue(created)
        self.assertTrue(all(instance._conn is None for instance in created))
        self.assertFalse(kernel._runtime_writer_lock.held)
        folder.cleanup()


class RuntimeShutdownTests(unittest.IsolatedAsyncioTestCase):
    """关闭失败时保留资源归属，避免遗失仍在工作的内核。"""

    async def test_failed_shutdown_retains_kernel_for_retry(self) -> None:
        instance = runtime.AMemorixRuntime()
        kernel = SimpleNamespace(shutdown=AsyncMock(side_effect=[RuntimeError("synthetic shutdown failure"), None]))
        instance._kernel = kernel
        instance._started = True
        await instance.stop()
        self.assertIs(instance._kernel, kernel)
        self.assertFalse(instance.available)
        self.assertIsNone(await instance.get_kernel())
        await instance.stop()
        self.assertIsNone(instance._kernel)
        self.assertFalse(instance._started)
        self.assertEqual(instance.error, "")

    async def test_stopping_kernel_is_not_returned_to_new_requests(self) -> None:
        entered, release = asyncio.Event(), asyncio.Event()

        async def shutdown():
            entered.set()
            await release.wait()

        instance = runtime.AMemorixRuntime()
        instance._kernel = SimpleNamespace(shutdown=shutdown)
        instance._started = True
        stopping = asyncio.create_task(instance.stop())
        await entered.wait()
        self.assertFalse(instance.available)
        self.assertIsNone(await instance.get_kernel())
        release.set()
        await stopping


class SummaryTimeTests(unittest.IsolatedAsyncioTestCase):
    """摘要资料时间使用 UTC，不受宿主机本地时区影响。"""

    async def test_summary_cutoff_marks_naive_utc_before_timestamp_conversion(self) -> None:
        class UTCDateTime(datetime):
            """阻止无时区时间偷偷使用宿主机本地时区。"""

            def timestamp(self):
                if self.tzinfo is None:
                    raise AssertionError("naive UTC must be made aware")
                return super().timestamp()

        bridge = writeback.ChatWriteback()
        bridge._checkpoint = AsyncMock(return_value=0)
        config = SimpleNamespace(
            integration=SimpleNamespace(
                chat_summary_writeback_message_threshold=3, chat_summary_writeback_context_length=6
            )
        )
        occurred_at = UTCDateTime(2026, 10, 29, 12, 0, 0)
        with (
            patch.object(writeback, "count_messages", AsyncMock(return_value=3)),
            patch.object(writeback.amemorix_runtime, "config", return_value=config),
            patch.object(writeback, "ingest_summary", AsyncMock(return_value={"success": True})) as ingest,
        ):
            await bridge._handle_summary({"tenant": "test:time", "occurred_at": occurred_at})
        self.assertEqual(
            ingest.await_args.kwargs["time_end"], datetime(2026, 10, 29, 12, tzinfo=timezone.utc).timestamp()
        )

    async def test_native_profile_payload_remains_available_to_injection(self) -> None:
        kernel = SimpleNamespace(
            get_person_profile=AsyncMock(
                return_value={
                    "person_id": "person",
                    "person_name": "小林",
                    "summary": "小林喜欢登山",
                    "evidence": [],
                }
            )
        )
        proxy = SimpleNamespace(available=True, get_kernel=AsyncMock(return_value=kernel))
        with patch.object(service, "amemorix_runtime", proxy):
            result = await service.person_profile("test:profile", "person")
        self.assertTrue(result["success"])
        self.assertEqual(result["summary"], "小林喜欢登山")
