"""P6：表情与控制台接口。"""

from __future__ import annotations

import base64
import tempfile
import unittest
from io import BytesIO
from uuid import uuid4
from types import SimpleNamespace
from pathlib import Path
from datetime import timedelta
from unittest.mock import AsyncMock, patch

from PIL import Image
from fastapi import HTTPException
from src.plugins.nonebot_plugin_fxbot.db import base_models
from src.plugins.nonebot_plugin_fxbot.chat import emoji as emoji_module
from src.plugins.nonebot_plugin_fxbot.chat.tools import ToolRuntime
from src.plugins.nonebot_plugin_fxbot.chat.types import ChatRequest, ChatResponse
from src.plugins.nonebot_plugin_fxbot.chat.memory import migrate
from src.plugins.nonebot_plugin_fxbot.chat.service import ChatService
from src.plugins.nonebot_plugin_fxbot.chat.session import ChatSessionStore
from src.plugins.nonebot_plugin_fxbot.console.routes import chat as chat_routes
from src.plugins.nonebot_plugin_fxbot.chat.memory.models import utc_now_naive
from src.plugins.nonebot_plugin_fxbot.chat.message_receive import sender as sender_module
from src.plugins.nonebot_plugin_fxbot.chat.memory.repository import chat_repository
from src.plugins.nonebot_plugin_fxbot.config.system_defaults import (
    SYSTEM_DEFAULTS,
    validate_system_config,
)

_PNG = b"\x89PNG\r\n\x1a\n" + b"test-image"


def _image_png(size: tuple[int, int] = (640, 480)) -> bytes:
    """生成一张真实 PNG，用于缩略图测试。"""
    buffer = BytesIO()
    Image.new("RGB", size, "red").save(buffer, "PNG")
    return buffer.getvalue()


class _FakeConfig:
    """按模块提供 chat 子配置。"""

    def __init__(self, **chat: object) -> None:
        self.chat = chat

    def get_system(self) -> dict[str, object]:
        return {"chat": self.chat}


def _tenant() -> str:
    return f"test:{uuid4().hex}"


async def _prepare_db() -> None:
    """初始化临时数据库。"""
    await base_models.init_database()
    await migrate.ensure_schema()


class EmojiDbTests(unittest.IsolatedAsyncioTestCase):
    """表情入库、挑选、冷却与文件清理。"""

    async def asyncSetUp(self) -> None:
        await _prepare_db()
        self.tenant = _tenant()
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.addCleanup(patch.stopall)
        patcher = patch.object(emoji_module, "emoji_dir", return_value=Path(self.folder.name))
        patcher.start()
        patcher = patch.object(
            emoji_module,
            "get_config_manager",
            return_value=_FakeConfig(emoji={}),
        )
        patcher.start()

    async def test_save_duplicate_and_pick(self) -> None:
        row = await emoji_module.save_emoji(self.tenant, _PNG, description="开心大笑", tags=["开心"])
        self.assertTrue(Path(row.path).exists())
        again = await emoji_module.save_emoji(self.tenant, _PNG, description="换了描述", tags=["开心"])
        self.assertEqual(row.hash, again.hash)
        self.assertEqual(again.description, "换了描述")
        self.assertEqual(len(await chat_repository.list_emoji(self.tenant)), 1)

        picked = await emoji_module.pick_emoji(self.tenant, "今天真开心")
        self.assertIsNotNone(picked)
        self.assertEqual(picked.hash, row.hash)

    async def test_collage_returns_numbered_grid(self) -> None:
        rows = []
        for index in range(2):
            target = Path(self.folder.name) / f"c{index}.png"
            target.write_bytes(_image_png())
            rows.append(SimpleNamespace(path=str(target)))

        uri = emoji_module.emoji_collage(rows)
        self.assertTrue(uri and uri.startswith("data:image/png;base64,"))
        with Image.open(BytesIO(base64.b64decode(uri.split(",", 1)[1]))) as image:
            self.assertEqual(image.size, (emoji_module._COLLAGE_TILE * 2, emoji_module._COLLAGE_TILE))
        self.assertIsNone(emoji_module.emoji_collage([]))

    async def test_cooldown_blocks_reuse(self) -> None:
        row = await emoji_module.save_emoji(self.tenant, _PNG, description="开心大笑")
        now = utc_now_naive()
        await chat_repository.mark_emoji_used(self.tenant, row.hash, now=now)

        self.assertIsNone(await emoji_module.pick_emoji(self.tenant, "开心", now=now + timedelta(minutes=1)))
        picked = await emoji_module.pick_emoji(self.tenant, "开心", now=now + emoji_module.EMOJI_COOLDOWN)
        self.assertIsNotNone(picked)

    async def test_unmatched_returns_none(self) -> None:
        await emoji_module.save_emoji(self.tenant, _PNG, description="happy")
        self.assertIsNone(await emoji_module.pick_emoji(self.tenant, "zzzz"))

    async def test_rejects_non_image_and_removes_file(self) -> None:
        with self.assertRaises(ValueError):
            await emoji_module.save_emoji(self.tenant, b"not an image")
        row = await emoji_module.save_emoji(self.tenant, _PNG, description="开心")
        emoji_module.remove_emoji_files(row.path, row.hash)
        self.assertFalse(Path(row.path).exists())
        emoji_module.remove_emoji_files(str(Path(self.folder.name).parent / "outside.txt"), "outside")

    async def test_thumbnail_is_cached_and_removed(self) -> None:
        row = await emoji_module.save_emoji(self.tenant, _image_png(), description="大图")
        thumb = emoji_module.emoji_thumbnail(row.hash, row.path)
        self.assertTrue(thumb.is_file())
        with Image.open(thumb) as image:
            self.assertLessEqual(max(image.size), emoji_module._THUMB_SIZE)
        self.assertEqual(emoji_module.emoji_thumbnail(row.hash, row.path), thumb)
        emoji_module.remove_emoji_files(row.path, row.hash)
        self.assertFalse(thumb.exists())


class ServiceEmojiTests(unittest.IsolatedAsyncioTestCase):
    """正文发送后补表情；失败不影响正文。"""

    def _request(self) -> ChatRequest:
        return ChatRequest(
            session_id="tenant",
            user_id="user",
            group_id="group",
            text="你好",
            turn_id="turn",
            dedupe_key="msg:1",
            trigger_kind="mention",
        )

    async def test_send_emoji_once(self) -> None:
        row = SimpleNamespace(tenant="tenant", hash="hash", path="x.png")
        matcher = SimpleNamespace(sent=[], send=AsyncMock())
        service = ChatService(ChatSessionStore())
        with (
            patch.object(sender_module, "emoji_config", return_value={}),
            patch.object(sender_module, "pick_emoji", new=AsyncMock(return_value=row)),
            patch.object(sender_module, "mark_used", new=AsyncMock()) as used,
            patch.object(sender_module, "build_message", return_value="built"),
            patch.object(sender_module, "build_message_segment", return_value="segment"),
        ):
            await service._send_emoji(
                self._request(),
                ChatResponse(text="干净文本", epoch=0),
                ToolRuntime(bot=object()),
                matcher,
                1,
            )

        matcher.send.assert_awaited_once_with("built")
        used.assert_awaited_once_with(row)

    async def test_send_failure_is_swallowed(self) -> None:
        row = SimpleNamespace(tenant="tenant", hash="hash", path="x.png")
        matcher = SimpleNamespace(send=AsyncMock(side_effect=RuntimeError("bad")))
        service = ChatService(ChatSessionStore())
        with (
            patch.object(sender_module, "emoji_config", return_value={}),
            patch.object(sender_module, "pick_emoji", new=AsyncMock(return_value=row)),
            patch.object(sender_module, "mark_used", new=AsyncMock()) as used,
            patch.object(sender_module, "build_message", return_value="built"),
            patch.object(sender_module, "build_message_segment", return_value="segment"),
        ):
            await service._send_emoji(
                self._request(), ChatResponse(text="x", epoch=0), ToolRuntime(bot=object()), matcher, 1
            )

        used.assert_not_awaited()

    async def test_no_bot_sends_nothing(self) -> None:
        service = ChatService(ChatSessionStore())
        await service._send_emoji(
            self._request(), ChatResponse(text="x", epoch=0), ToolRuntime(bot=None), SimpleNamespace(), 1
        )


class ConsoleP6Tests(unittest.IsolatedAsyncioTestCase):
    """控制台新接口的基本形状。"""

    async def test_emoji_payload_and_upload(self) -> None:
        payload = chat_routes._emoji_payload(
            SimpleNamespace(
                hash="h",
                description="开心",
                tags='["大笑"]',
                count=2,
                last_used=None,
                enabled=True,
                created_at=None,
            )
        )
        self.assertEqual(payload["tags"], ["大笑"])
        self.assertNotIn("path", payload)

        row = SimpleNamespace(
            hash="h", description="开心", tags="[]", count=0, last_used=None, enabled=True, created_at=None
        )
        content = base64.b64encode(_PNG).decode()
        with (
            patch.object(chat_routes, "_describe_upload", new=AsyncMock(return_value=("开心", ["大笑"]))),
            patch.object(chat_routes.emoji_module, "save_emoji", new=AsyncMock(return_value=row)) as save,
        ):
            result = await chat_routes.upload_emoji({"tenant": "t", "content": content})
        self.assertEqual(result["item"]["hash"], "h")
        save.assert_awaited_once_with("t", _PNG, description="开心", tags=["大笑"])

        with self.assertRaises(HTTPException):
            await chat_routes.upload_emoji({"tenant": "t", "content": "!!!"})

    async def test_batch_delete_emoji(self) -> None:
        rows = [SimpleNamespace(hash="a", path="a.png"), None]
        with (
            patch.object(chat_routes.chat_repository, "delete_emoji", new=AsyncMock(side_effect=rows)) as deleted,
            patch.object(chat_routes.emoji_module, "remove_emoji_files") as remove,
        ):
            result = await chat_routes.batch_delete_emoji({"tenant": "t", "hashes": ["a", "b", "a"]})
        self.assertEqual(result["deleted"], 1)
        self.assertEqual(deleted.await_count, 2)
        remove.assert_called_once_with("a.png", "a")
        with self.assertRaises(HTTPException):
            await chat_routes.batch_delete_emoji({"tenant": "t", "hashes": []})

    async def test_emoji_thumbnail_route(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        source = Path(folder.name) / "a.png"
        source.write_bytes(_image_png())
        row = SimpleNamespace(hash="h", path=str(source))
        with patch.object(chat_routes.chat_repository, "get_emoji", new=AsyncMock(return_value=row)):
            response = await chat_routes.emoji_thumbnail("h", "t")
        self.assertTrue(str(response.path).endswith(".thumb.jpg"))
        self.assertTrue(Path(response.path).is_file())
        with patch.object(chat_routes.chat_repository, "get_emoji", new=AsyncMock(return_value=None)):
            with self.assertRaises(HTTPException):
                await chat_routes.emoji_thumbnail("h", "t")

    async def test_emoji_list_filters_and_patch(self) -> None:
        row = SimpleNamespace(
            hash="h",
            description="开心",
            tags="[]",
            count=0,
            last_used=None,
            enabled=True,
            registered=True,
            vlm_processed=True,
            register_time=None,
            created_at=None,
        )
        counts = {"registered": 1, "pending": 0, "unidentified": 0, "disabled": 0, "all": 1}
        with (
            patch.object(chat_routes.chat_repository, "list_emoji", new=AsyncMock(return_value=[row])) as listed,
            patch.object(chat_routes.chat_repository, "count_emoji", new=AsyncMock(return_value=counts)),
        ):
            result = await chat_routes.list_emoji(
                "t", status="registered", search="开心", format="PNG", sort="count", order="asc"
            )
        self.assertEqual(result["counts"], counts)
        self.assertEqual(result["items"][0]["status"], "registered")
        listed.assert_awaited_once_with(
            "t",
            status="registered",
            search="开心",
            file_format="png",
            sort="count",
            descending=False,
        )

        with (
            patch.object(chat_routes.chat_repository, "get_emoji", new=AsyncMock(return_value=row)),
            patch.object(chat_routes.chat_repository, "set_emoji_enabled", new=AsyncMock(return_value=True)),
            patch.object(chat_routes.chat_repository, "set_emoji_registered", new=AsyncMock(return_value=True)),
            patch.object(
                chat_routes.chat_repository, "update_emoji_details", new=AsyncMock(return_value=row)
            ) as details,
        ):
            patched = await chat_routes.update_emoji(
                "h", "t", {"enabled": True, "registered": False, "description": " 新描述 ", "tags": [" 笑 ", ""]}
            )
        self.assertEqual(patched["item"]["hash"], "h")
        details.assert_awaited_once_with("t", "h", description="新描述", tags=["笑"])

    async def test_health_route(self) -> None:
        with (
            patch.object(chat_routes, "is_ready", return_value=True),
            patch.object(chat_routes, "degraded_reason", return_value=""),
            patch.object(chat_routes, "get_bots", return_value={}),
            patch.object(chat_routes, "_scheduler_jobs", return_value=[{"id": "fxbot_chat_memory_extract"}]),
            patch.object(
                chat_routes,
                "get_config_manager",
                return_value=_FakeConfig(enabled=True),
            ),
        ):
            health = await chat_routes.chat_health()

        self.assertTrue(health["enabled"])
        self.assertTrue(health["storage_ready"])
        self.assertTrue(health["scheduler_ready"])
        self.assertEqual(health["online_bots"], 0)


class P6ConfigTests(unittest.TestCase):
    """新增配置默认值与校验。"""

    def test_defaults(self) -> None:
        chat = SYSTEM_DEFAULTS["chat"]
        self.assertTrue(chat["emoji"]["cache_cleanup"]["enabled"])

    def test_validation(self) -> None:
        with self.assertRaises(ValueError):
            validate_system_config({"chat": {"emoji": []}})
