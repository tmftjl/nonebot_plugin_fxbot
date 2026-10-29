"""原图持久化、图片任务恢复和会话查询边界。"""

from __future__ import annotations

import json
import base64
import tempfile
import unittest
from io import BytesIO
from uuid import uuid4
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import AsyncMock, patch

from PIL import Image
from src.plugins.nonebot_plugin_fxbot.db import base_models
from src.plugins.nonebot_plugin_fxbot.chat.memory import migrate
from src.plugins.nonebot_plugin_fxbot.chat.amemorix import image_writeback as bridge_module
from src.plugins.nonebot_plugin_fxbot.chat.tools.types import ToolError, ToolContext
from src.plugins.nonebot_plugin_fxbot.chat.memory.models import ChatMessage
from src.plugins.nonebot_plugin_fxbot.chat.amemorix._host import llm as host_llm
from src.plugins.nonebot_plugin_fxbot.chat.tools.amemorix import query_image_memory_tool
from src.plugins.nonebot_plugin_fxbot.chat.memory.repository import chat_repository
from src.plugins.nonebot_plugin_fxbot.chat.amemorix.image_journal import ImageWritebackJournal
from src.plugins.nonebot_plugin_fxbot.chat.amemorix.core.image.embedder import HostImageEmbedder


class ImageEmbedderBridgeTests(unittest.IsolatedAsyncioTestCase):
    """图片向量门面必须导入实际宿主模块，未配置模型按不可用处理。"""

    async def test_request_reaches_host_embedding_client(self) -> None:
        client = SimpleNamespace(
            embed_image=AsyncMock(
                return_value=host_llm.EmbeddingResult(
                    embedding=[3.0, 4.0], model_name="image-model", model_identifier="image-model", api_provider="test"
                )
            )
        )
        with patch.object(host_llm, "EmbeddingServiceClient", return_value=client) as constructor:
            result = await HostImageEmbedder()._request(b"image", mime_type="image/png", session_id="own-chat")
        self.assertAlmostEqual(float(result["embedding"][0]), 0.6)
        self.assertAlmostEqual(float(result["embedding"][1]), 0.8)
        self.assertEqual(constructor.call_args.kwargs["session_id"], "own-chat")
        self.assertEqual(client.embed_image.await_args.args[0], b"image")

    async def test_no_embedding_reports_configuration_instead_of_import_failure(self) -> None:
        with patch.object(host_llm, "embedding_provider_name", side_effect=RuntimeError("未配置图片嵌入模型")):
            result = await HostImageEmbedder().probe()
        self.assertFalse(result["available"])
        self.assertEqual(result["reason"], "RuntimeError")
        self.assertEqual(result["message"], "未配置图片嵌入模型")


class ImageMemoryBridgeTests(unittest.IsolatedAsyncioTestCase):
    """使用真实临时文件和数据库验证宿主图片接入。"""

    async def asyncSetUp(self) -> None:
        await base_models.init_database()
        await migrate.ensure_schema()
        self.tenant = f"test:image-memory:{uuid4().hex}"
        self.addAsyncCleanup(chat_repository.clear_tenant, self.tenant)
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.bridge = bridge_module.ImageWriteback()
        self.addAsyncCleanup(self.bridge.stop)
        self.kernel = SimpleNamespace(image_memory=AsyncMock(return_value={"success": True, "hits": []}))
        config = SimpleNamespace(image_memory=SimpleNamespace(enabled=True, max_bytes=1024 * 1024))
        self.runtime = SimpleNamespace(
            available=True, config=lambda: config, get_kernel=AsyncMock(return_value=self.kernel)
        )
        self.enterContext(patch.object(bridge_module, "amemorix_runtime", self.runtime))
        self.enterContext(patch.object(bridge_module, "data_dir", return_value=Path(self.folder.name)))
        buffer = BytesIO()
        Image.new("RGB", (16, 16), "red").save(buffer, "PNG")
        self.image = buffer.getvalue()

    async def test_original_bytes_and_component_path_survive_writeback(self) -> None:
        encoded = "data:image/png;base64," + base64.b64encode(self.image).decode("ascii")
        items = await self.bridge.prepare(self.tenant, [encoded], ["2"])
        self.assertEqual(Path(items[0]["path"]).read_bytes(), self.image)
        await chat_repository.append_message(
            ChatMessage(
                tenant=self.tenant,
                dedupe_key="msg:image",
                platform_message_id="image",
                content="这是我的自行车",
                payload=json.dumps({"image_memory": items}),
            )
        )
        await self.bridge.process({"session_id": self.tenant, "message_id": "image"})
        arguments = self.kernel.image_memory.await_args.kwargs
        self.assertEqual(arguments["chat_id"], self.tenant)
        self.assertEqual(arguments["component_path"], "2")
        self.assertEqual(arguments["image_bytes"], self.image)
        self.assertEqual(arguments["user_statement"], "这是我的自行车")

    async def test_missing_or_other_session_message_does_not_ingest(self) -> None:
        await self.bridge.process({"session_id": self.tenant, "message_id": "missing"})
        self.kernel.image_memory.assert_not_awaited()

    def test_journal_pending_job_survives_restart(self) -> None:
        path = Path(self.folder.name) / "journal.db"
        journal = ImageWritebackJournal(path)
        journal.enqueue(self.tenant, "image")
        journal.close()
        restarted = ImageWritebackJournal(path)
        self.addCleanup(restarted.close)
        self.assertEqual(restarted.next_job()["message_id"], "image")
        restarted.complete(self.tenant, "image")
        self.assertIsNone(restarted.next_job())

    async def test_image_query_rejects_foreign_message(self) -> None:
        from src.plugins.nonebot_plugin_fxbot.chat.tools import amemorix as tools

        context = ToolContext(user_id="u1", group_id="g1", session_id=self.tenant, platform="test")
        with patch.object(tools, "amemorix_runtime", self.runtime):
            with self.assertRaises(ToolError):
                await query_image_memory_tool(context, object(), "foreign-message", "0")
        self.kernel.image_memory.assert_not_awaited()
