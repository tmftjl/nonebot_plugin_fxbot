"""系统配置有效历史与会员闸门回退回归测试。"""

from __future__ import annotations

import os
import json
import unittest
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from src.plugins.nonebot_plugin_fxbot.utils.cache import cache
from src.plugins.nonebot_plugin_fxbot.config.manager import ConfigManager
from src.plugins.nonebot_plugin_fxbot.config.system_defaults import SYSTEM_DEFAULTS, validate_system_config


class LastValidSystemConfigTests(unittest.TestCase):
    """验证坏配置不会关闭已有会员限制或清空安全设置。"""

    def setUp(self) -> None:
        directory = TemporaryDirectory(prefix="fxbot-config-history-")
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "config.json"
        self.manager = self._new_manager()
        self.proxy = self.manager.register("system")
        cache.pop(self.proxy._cache_key)
        self.addCleanup(cache.pop, self.proxy._cache_key)
        self.valid = deepcopy(SYSTEM_DEFAULTS)
        self.valid["membership"]["enabled"] = True
        self.valid["membership"]["free_bot_ids"] = ["free-bot"]
        self.valid["system"]["token"] = "test-token"
        self.valid["system"]["bot_admins"] = ["admin"]

    def _new_manager(self) -> ConfigManager:
        """创建使用相同磁盘文件但不共享内存历史的管理器。"""
        manager = ConfigManager()
        proxy = manager.register("system", SYSTEM_DEFAULTS, validator=validate_system_config)
        proxy.storage._path = self.path
        return manager

    def _write_bad(self, content: str) -> None:
        """模拟外部编辑，并确保热重载能观察到文件变更。"""
        previous_mtime = self.proxy.storage.mtime()
        self.path.write_text(content, encoding="utf-8")
        os.utime(self.path, (previous_mtime + 2, previous_mtime + 2))

    def test_get_system_preserves_valid_config_after_bad_edit(self) -> None:
        self.proxy.save(self.valid)
        self.assertEqual(self.manager.get_system(), self.valid)
        self._write_bad('{"membership": {"enabled": false}, "chat": {"max_context_size": -1}}')
        with patch("src.plugins.nonebot_plugin_fxbot.config.proxy.logger.warning") as warning:
            self.assertEqual(self.manager.get_system(), self.valid)
        self.assertIn("max_context_size", warning.call_args.args[0])
        self.assertIn("上一份有效配置", warning.call_args.args[0])
        self.assertEqual(self.proxy.history_storage.read(strict=True), self.valid)

    def test_external_valid_load_is_preserved_after_cache_eviction(self) -> None:
        self.proxy.storage.write(self.valid)
        self.assertEqual(self.manager.get_system(), self.valid)
        cache.pop(self.proxy._cache_key)
        self.proxy.history_storage.path.unlink()
        self._write_bad('{"chat": {"expression": null}}')
        self.assertEqual(self.manager.get_system(), self.valid)

    def test_restart_recovers_from_disk_history(self) -> None:
        self.proxy.save(self.valid)
        self._write_bad('{"chat": {"max_context_size": -1}}')
        cache.pop(self.proxy._cache_key)
        restarted = self._new_manager()
        restarted.bootstrap()
        self.assertEqual(restarted.get_system(), self.valid)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), {"chat": {"max_context_size": -1}})

    def test_broken_json_and_non_object_do_not_overwrite_primary_or_history(self) -> None:
        self.proxy.save(self.valid)
        for content in ('{"chat":', "[]", "null", ""):
            with self.subTest(content=content):
                self._write_bad(content)
                cache.pop(self.proxy._cache_key)
                restarted = self._new_manager()
                restarted.bootstrap()
                self.assertEqual(restarted.get_system(), self.valid)
                self.assertEqual(self.path.read_text(encoding="utf-8"), content)
                self.assertEqual(self.proxy.history_storage.read(strict=True), self.valid)

    def test_missing_primary_uses_disk_history(self) -> None:
        self.proxy.save(self.valid)
        self.path.unlink()
        cache.pop(self.proxy._cache_key)
        restarted = self._new_manager()
        restarted.bootstrap()
        self.assertEqual(restarted.get_system(), self.valid)
        self.assertFalse(self.path.exists())

    def test_first_load_without_history_uses_defaults_without_overwrite(self) -> None:
        self._write_bad('{"chat": {"max_context_size": -1}}')
        self.assertEqual(self.manager.get_system(), SYSTEM_DEFAULTS)
        self.assertFalse(self.proxy.history_storage.exists())
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), {"chat": {"max_context_size": -1}})

    def test_first_missing_config_creates_defaults(self) -> None:
        self.assertEqual(self.manager.get_system(), SYSTEM_DEFAULTS)
        self.assertEqual(self.proxy.storage.read(strict=True), SYSTEM_DEFAULTS)

    def test_reload_reports_invalid_config_and_returns_valid_history(self) -> None:
        self.proxy.save(self.valid)
        self._write_bad('{"chat": {"max_context_size": -1}}')
        ok, result = self.manager.reload_all()
        self.assertFalse(ok)
        self.assertFalse(result["system/config.json"]["ok"])
        self.assertIn("max_context_size", result["system/config.json"]["error"])
        self.assertEqual(result["system/config.json"]["data"], self.valid)
        self.assertEqual(self.manager.get_system(), self.valid)

    def test_valid_edit_after_failure_updates_history_and_allows_disabling(self) -> None:
        self.proxy.save(self.valid)
        self._write_bad('{"chat": {"max_context_size": -1}}')
        self.assertEqual(self.manager.get_system(), self.valid)
        updated = deepcopy(self.valid)
        updated["membership"]["enabled"] = False
        self._write_bad(json.dumps(updated))
        self.assertEqual(self.manager.get_system(), updated)
        self.assertEqual(self.proxy.history_storage.read(strict=True), updated)

    def test_invalid_save_preserves_primary_memory_and_history(self) -> None:
        self.proxy.save(self.valid)
        with self.assertRaises(ValueError):
            self.proxy.save({"chat": {"expression": None}})
        self.assertEqual(self.manager.get_system(), self.valid)
        self.assertEqual(self.proxy.storage.read(strict=True), self.valid)
        self.assertEqual(self.proxy.history_storage.read(strict=True), self.valid)

    def test_bootstrap_does_not_write_invalid_migration(self) -> None:
        self.proxy.save(self.valid)
        content = '{"chat": {"web_search": {}, "max_context_size": -1}}'
        self._write_bad(content)
        self.manager.bootstrap()
        self.assertEqual(self.manager.get_system(), self.valid)
        self.assertEqual(self.path.read_text(encoding="utf-8"), content)

    def test_bootstrap_rejects_complete_invalid_config_without_overwriting_history(self) -> None:
        self.proxy.save(self.valid)
        invalid = deepcopy(self.valid)
        invalid["membership"]["enabled"] = False
        invalid["chat"]["max_context_size"] = -1
        self._write_bad(json.dumps(invalid))
        self.manager.bootstrap()
        self.assertEqual(self.manager.get_system(), self.valid)
        self.assertEqual(self.proxy.storage.read(strict=True), invalid)
        self.assertEqual(self.proxy.history_storage.read(strict=True), self.valid)

    def test_valid_bootstrap_migration_preserves_membership_and_security(self) -> None:
        legacy = deepcopy(self.valid)
        legacy["chat"]["web_search"] = {"api_key": "legacy-key"}
        self.proxy.storage.write(legacy)
        self.manager.bootstrap()
        migrated = self.manager.get_system()
        self.assertEqual(migrated["membership"], self.valid["membership"])
        self.assertEqual(migrated["system"], self.valid["system"])
        self.assertNotIn("web_search", migrated["chat"])
        self.assertEqual(migrated["chat"]["model_routing"]["web_search"], "deepseek")
        self.assertEqual(self.proxy.history_storage.read(strict=True), migrated)

    def test_invalid_history_refuses_default_fallback(self) -> None:
        self.proxy.save(self.valid)
        self._write_bad('{"chat": {"max_context_size": -1}}')
        self.proxy.history_storage.path.write_text("[]", encoding="utf-8")
        cache.pop(self.proxy._cache_key)
        with self.assertRaisesRegex(RuntimeError, "拒绝回退默认值"):
            self._new_manager().get_system()

    def test_returned_config_does_not_mutate_valid_history(self) -> None:
        self.proxy.save(self.valid)
        self.manager.get_system()["membership"]["enabled"] = False
        self._write_bad('{"chat": {"max_context_size": -1}}')
        self.assertEqual(self.manager.get_system(), self.valid)

    def test_failed_atomic_replace_preserves_previous_file(self) -> None:
        self.proxy.save(self.valid)
        with (
            patch("src.plugins.nonebot_plugin_fxbot.config.storage.os.replace", side_effect=OSError("disk error")),
            self.assertRaises(OSError),
        ):
            self.proxy.save(SYSTEM_DEFAULTS)
        self.assertEqual(self.manager.get_system(), self.valid)
        self.assertEqual(self.proxy.storage.read(strict=True), self.valid)
        self.assertEqual(self.proxy.history_storage.read(strict=True), self.valid)
        self.assertEqual(len(list(self.path.parent.iterdir())), 2)

    def test_failed_snapshot_replace_preserves_history_and_does_not_publish_config(self) -> None:
        self.proxy.save(self.valid)
        updated = deepcopy(self.valid)
        updated["membership"]["enabled"] = False
        self._write_bad(json.dumps(updated))
        with (
            patch("src.plugins.nonebot_plugin_fxbot.config.storage.os.replace", side_effect=OSError("disk error")),
            patch("src.plugins.nonebot_plugin_fxbot.config.proxy.logger.warning") as warning,
            self.assertRaises(OSError),
        ):
            self.manager.get_system()
        self.assertIn("disk error", warning.call_args.args[0])
        self.assertEqual(self.proxy.history_storage.read(strict=True), self.valid)
        self._write_bad('{"chat": {"max_context_size": -1}}')
        self.assertEqual(self.manager.get_system(), self.valid)
