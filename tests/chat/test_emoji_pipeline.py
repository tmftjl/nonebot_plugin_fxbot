"""表情包采集、状态机、名额替换与缓存清理。"""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from uuid import uuid4
from pathlib import Path
from datetime import timedelta
from unittest.mock import AsyncMock, patch

from src.plugins.nonebot_plugin_fxbot.db import base_models
from src.plugins.nonebot_plugin_fxbot.chat import emoji as emoji_module
from src.plugins.nonebot_plugin_fxbot.chat.memory import migrate
from src.plugins.nonebot_plugin_fxbot.chat.memory.models import utc_now_naive
from src.plugins.nonebot_plugin_fxbot.chat.memory.repository import chat_repository

_PNG = b"\x89PNG\r\n\x1a\n" + b"emoji-image"


class _FakeConfig:
    """按模块提供 chat 子配置。"""

    def __init__(self, **chat: object) -> None:
        self.chat = chat

    def get_system(self) -> dict[str, object]:
        return {"chat": self.chat}


class _FakeProvider:
    """替代 provider_manager.lease 的异步上下文。"""

    def __init__(self, content: str = "") -> None:
        self.chat = AsyncMock(return_value=type("Response", (), {"content": content})())

    async def __aenter__(self) -> "_FakeProvider":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False


def _tenant() -> str:
    return f"test:{uuid4().hex}"


async def _prepare_db() -> None:
    await base_models.init_database()
    await migrate.ensure_schema()


class EmojiPipelineTests(unittest.IsolatedAsyncioTestCase):
    """从消息采集到注册、替换和清理的完整链路。"""

    async def asyncSetUp(self) -> None:
        await _prepare_db()
        self.tenant = _tenant()
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.addCleanup(patch.stopall)
        patch.object(emoji_module, "emoji_dir", return_value=Path(self.folder.name)).start()
        self.config = {
            "steal_emoji": True,
            "max_reg_num": 64,
            "do_replace": True,
            "content_filtration": False,
            "max_emoji_size_mb": 5,
            "cache_cleanup": {
                "enabled": True,
                "emoji_file_retention_days": 30,
                "no_file_record_retention_days": 30,
            },
        }
        patch.object(
            emoji_module,
            "get_config_manager",
            return_value=_FakeConfig(emoji=self.config),
        ).start()

    async def test_collect_then_analyze_registers(self) -> None:
        with (
            patch.object(emoji_module, "emoji_sources_for_event", return_value=[_PNG]),
            patch.object(emoji_module, "load_image_bytes", new=AsyncMock(return_value=_PNG)),
            patch.object(emoji_module, "_schedule_analysis") as schedule,
        ):
            await emoji_module.collect_from_event(self.tenant, object())

        digest = hashlib.sha256(_PNG).hexdigest()
        row = await chat_repository.get_emoji(self.tenant, digest)
        self.assertIsNotNone(row)
        self.assertFalse(row.registered)
        self.assertFalse(row.vlm_processed)
        self.assertEqual(emoji_module.emoji_status(row), "unidentified")
        schedule.assert_called_once()

        with (
            patch.object(emoji_module, "fallback_provider", lambda *args: _FakeProvider()),
            patch.object(
                emoji_module,
                "describe_emoji",
                new=AsyncMock(return_value=("开心大笑", ["开心"])),
            ),
        ):
            await emoji_module._analyze(self.tenant, digest, Path(row.path))

        row = await chat_repository.get_emoji(self.tenant, digest)
        self.assertTrue(row.registered)
        self.assertTrue(row.vlm_processed)
        self.assertEqual(row.description, "开心大笑")
        self.assertEqual(emoji_module.emoji_status(row), "registered")
        self.assertIsNotNone(row.register_time)

    async def test_content_filtration_keeps_pending(self) -> None:
        with (
            patch.object(emoji_module, "emoji_sources_for_event", return_value=[_PNG]),
            patch.object(emoji_module, "load_image_bytes", new=AsyncMock(return_value=_PNG)),
            patch.object(emoji_module, "_schedule_analysis"),
        ):
            await emoji_module.collect_from_event(self.tenant, object())

        digest = hashlib.sha256(_PNG).hexdigest()
        row = await chat_repository.get_emoji(self.tenant, digest)
        self.config["content_filtration"] = True
        with (
            patch.object(emoji_module, "fallback_provider", lambda *args: _FakeProvider()),
            patch.object(emoji_module, "describe_emoji", new=AsyncMock(return_value=("描述", []))),
            patch.object(emoji_module, "review_emoji", new=AsyncMock(return_value=False)),
        ):
            await emoji_module._analyze(self.tenant, digest, Path(row.path))

        row = await chat_repository.get_emoji(self.tenant, digest)
        self.assertFalse(row.registered)
        self.assertTrue(row.vlm_processed)
        self.assertEqual(emoji_module.emoji_status(row), "pending")

    async def test_status_counts_and_filters(self) -> None:
        saved = await emoji_module.save_emoji(self.tenant, _PNG, description="已注册")
        pending = await chat_repository.save_collected_emoji(
            self.tenant, "p" * 64, str(Path(self.folder.name) / "p.png")
        )
        unidentified = await chat_repository.save_collected_emoji(
            self.tenant, "u" * 64, str(Path(self.folder.name) / "u.png")
        )
        await chat_repository.set_emoji_analysis(
            self.tenant, pending.hash, description="待审核", tags=["待"], passed=False
        )
        disabled = await emoji_module.save_emoji(self.tenant, _PNG + b"x", description="禁用")
        await chat_repository.set_emoji_enabled(self.tenant, disabled.hash, False)
        self.assertEqual(emoji_module.emoji_status(unidentified), "unidentified")

        counts = await chat_repository.count_emoji(self.tenant)
        self.assertEqual(counts["all"], 4)
        self.assertEqual(counts["registered"], 1)
        self.assertEqual(counts["pending"], 1)
        self.assertEqual(counts["unidentified"], 1)
        self.assertEqual(counts["disabled"], 1)

        registered = await chat_repository.list_emoji(self.tenant, status="registered")
        self.assertEqual([row.hash for row in registered], [saved.hash])
        search = await chat_repository.list_emoji(self.tenant, search="待审核")
        self.assertEqual([row.hash for row in search], [pending.hash])
        by_hash = await chat_repository.list_emoji(self.tenant, search=saved.hash[:12])
        self.assertEqual([row.hash for row in by_hash], [saved.hash])
        png_only = await chat_repository.list_emoji(self.tenant, file_format="png")
        self.assertEqual(len(png_only), 4)
        self.assertEqual(await chat_repository.list_emoji(self.tenant, file_format="gif"), [])

    async def test_reupload_re_enables_disabled_emoji(self) -> None:
        saved = await emoji_module.save_emoji(self.tenant, _PNG, description="旧描述")
        await chat_repository.set_emoji_enabled(self.tenant, saved.hash, False)

        again = await emoji_module.save_emoji(self.tenant, _PNG, description="重新上传")

        self.assertTrue(again.enabled)
        self.assertTrue(again.registered)
        self.assertEqual(again.description, "重新上传")

    async def test_manual_registration_state(self) -> None:
        row = await chat_repository.save_collected_emoji(self.tenant, "a" * 64, str(Path(self.folder.name) / "a.png"))
        self.assertEqual(emoji_module.emoji_status(row), "unidentified")
        await chat_repository.set_emoji_analysis(self.tenant, row.hash, description="描述", tags=[], passed=False)
        await chat_repository.set_emoji_registered(self.tenant, row.hash, True)
        updated = await chat_repository.get_emoji(self.tenant, row.hash)
        self.assertEqual(emoji_module.emoji_status(updated), "registered")
        await chat_repository.set_emoji_registered(self.tenant, row.hash, False)
        updated = await chat_repository.get_emoji(self.tenant, row.hash)
        self.assertEqual(emoji_module.emoji_status(updated), "pending")

    async def test_ensure_slot_respects_limit_and_replace_switch(self) -> None:
        await emoji_module.save_emoji(self.tenant, _PNG, description="旧表情")
        self.config["max_reg_num"] = 1
        self.config["do_replace"] = False
        self.assertFalse(await emoji_module._ensure_slot(self.tenant, "新表情"))
        self.config["max_reg_num"] = 2
        self.assertTrue(await emoji_module._ensure_slot(self.tenant, "新表情"))

    async def test_replace_by_llm_unregisters_picked_emoji(self) -> None:
        old = await emoji_module.save_emoji(self.tenant, _PNG, description="旧表情")
        provider = _FakeProvider("取消注册编号1")
        with patch.object(emoji_module, "fallback_provider", lambda *args: provider):
            replaced = await emoji_module._replace_by_llm(self.tenant, "新表情", 1)
        self.assertTrue(replaced)
        updated = await chat_repository.get_emoji(self.tenant, old.hash)
        self.assertFalse(updated.registered)
        provider.chat.assert_awaited_once()

    async def test_cleanup_removes_old_unregistered_keeps_registered(self) -> None:
        registered = await emoji_module.save_emoji(self.tenant, _PNG, description="已注册")
        with (
            patch.object(emoji_module, "emoji_sources_for_event", return_value=[_PNG + b"y"]),
            patch.object(emoji_module, "load_image_bytes", new=AsyncMock(return_value=_PNG + b"y")),
            patch.object(emoji_module, "_schedule_analysis"),
        ):
            await emoji_module.collect_from_event(self.tenant, object())
        collected = await chat_repository.get_emoji(self.tenant, hashlib.sha256(_PNG + b"y").hexdigest())
        self.assertTrue(Path(collected.path).exists())

        now = utc_now_naive() + timedelta(days=60)
        stats = await emoji_module.cleanup_emoji_cache(now=now)
        self.assertGreaterEqual(stats["files"], 1)
        self.assertGreaterEqual(stats["records"], 1)
        self.assertFalse(Path(collected.path).exists())
        self.assertIsNone(await chat_repository.get_emoji(self.tenant, collected.hash))
        self.assertIsNotNone(await chat_repository.get_emoji(self.tenant, registered.hash))
        self.assertTrue(Path(registered.path).exists())

    async def test_cleanup_disabled_keeps_everything(self) -> None:
        self.config["cache_cleanup"] = {"enabled": False, "emoji_file_retention_days": 1}
        with (
            patch.object(emoji_module, "emoji_sources_for_event", return_value=[_PNG + b"z"]),
            patch.object(emoji_module, "load_image_bytes", new=AsyncMock(return_value=_PNG + b"z")),
            patch.object(emoji_module, "_schedule_analysis"),
        ):
            await emoji_module.collect_from_event(self.tenant, object())
        collected = await chat_repository.get_emoji(self.tenant, hashlib.sha256(_PNG + b"z").hexdigest())
        stats = await emoji_module.cleanup_emoji_cache(now=utc_now_naive() + timedelta(days=60))
        self.assertEqual(stats, {"files": 0, "records": 0})
        self.assertTrue(Path(collected.path).exists())

    async def test_reconcile_schedules_unanalyzed_rows(self) -> None:
        path = Path(self.folder.name) / "b.png"
        path.write_bytes(_PNG)
        row = await chat_repository.save_collected_emoji(self.tenant, "b" * 64, str(path))
        with patch.object(emoji_module, "_schedule_analysis") as schedule:
            await emoji_module.reconcile_emoji_cache()
        scheduled = [call.args[1] for call in schedule.call_args_list]
        self.assertIn(row.hash, scheduled)


class EmojiConfigTests(unittest.TestCase):
    """表情配置校验。"""

    def setUp(self) -> None:
        from copy import deepcopy

        from src.plugins.nonebot_plugin_fxbot.config.system_defaults import SYSTEM_DEFAULTS

        self.data = deepcopy(SYSTEM_DEFAULTS)

    def test_defaults_pass(self) -> None:
        from src.plugins.nonebot_plugin_fxbot.config.system_defaults import validate_system_config

        validate_system_config(self.data)

    def test_invalid_values_rejected(self) -> None:
        from src.plugins.nonebot_plugin_fxbot.config.system_defaults import validate_system_config

        cases = [
            ("emoji_send_num", 0),
            ("emoji_send_num", 65),
            ("max_reg_num", -1),
            ("max_emoji_size_mb", -1),
            ("check_interval", 0),
            ("steal_emoji", "yes"),
        ]
        for key, value in cases:
            with self.subTest(key=key, value=value):
                data = self.data.copy()
                data["chat"] = dict(self.data["chat"])
                data["chat"]["emoji"] = dict(self.data["chat"]["emoji"], **{key: value})
                with self.assertRaises(ValueError):
                    validate_system_config(data)

    def test_cleanup_validation(self) -> None:
        from src.plugins.nonebot_plugin_fxbot.config.system_defaults import validate_system_config

        for key, value in (
            ("check_interval_hours", 0),
            ("emoji_file_retention_days", 0),
            ("no_file_record_retention_days", 0),
            ("enabled", "yes"),
        ):
            with self.subTest(key=key):
                data = self.data.copy()
                data["chat"] = dict(self.data["chat"])
                data["chat"]["emoji"] = dict(
                    self.data["chat"]["emoji"],
                    cache_cleanup=dict(self.data["chat"]["emoji"]["cache_cleanup"], **{key: value}),
                )
                with self.assertRaises(ValueError):
                    validate_system_config(data)
